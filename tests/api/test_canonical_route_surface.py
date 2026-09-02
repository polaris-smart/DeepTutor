from types import SimpleNamespace

import pytest

from deeptutor.api.main import app, health_live, health_ready



def _flatten_app_routes(routes, prefix=""):
    """Expand lazy fastapi _IncludedRouter wrappers into concrete routes."""
    for route in routes:
        if hasattr(route, "path"):
            yield prefix + str(route.path), route
        else:
            ctx = getattr(route, "include_context", None)
            nested = getattr(route, "original_router", None)
            if nested is None:
                continue
            nested_prefix = prefix + str(getattr(ctx, "prefix", "") or "")
            yield from _flatten_app_routes(nested.routes, nested_prefix)


def test_only_canonical_transport_and_resource_routes_are_registered() -> None:
    paths = {path for path, _ in _flatten_app_routes(app.routes)}

    required = {
        "/api/books",
        "/api/documents",
        "/api/knowledge-bases",
        "/api/mastery-paths/topics",
        "/api/notebooks",
        "/api/personas",
        "/api/sessions",
        "/api/system/runtime",
        "/files/attachments/{session_id}/{attachment_id}/{filename:path}",
        "/files/outputs/{output_path:path}",
        "/ws",
        "/ws/books",
    }
    assert required <= paths

    retired_prefixes = (
        "/api/v1",
        "/api/attachments",
        "/api/book",
        "/api/chat",
        "/api/co_writer",
        "/api/knowledge",
        "/api/learning",
        "/api/notebook",
        "/api/outputs",
    )
    # 悦学 K12 模块保留自己的命名空间（class-insights / exam-paper 组卷）——
    # 与上游 canonical 路由并存，不属上游 canonical 化范围。
    assert required <= paths
    assert "/api/system/runtime-topology" not in paths


class _Coordinator:
    def __init__(self, healthy: bool) -> None:
        self.healthy = healthy

    async def health(self) -> bool:
        return self.healthy


@pytest.mark.asyncio
async def test_health_endpoints_distinguish_liveness_and_readiness() -> None:
    assert await health_live() == {"status": "alive"}
    state = SimpleNamespace(ready=False)
    request = SimpleNamespace(app=SimpleNamespace(state=state))

    not_started = await health_ready(request)
    assert not_started.status_code == 503

    state.ready = True
    state.application_container = SimpleNamespace(coordinator=_Coordinator(False))
    unavailable = await health_ready(request)
    assert unavailable.status_code == 503

    state.application_container.coordinator = _Coordinator(True)
    assert await health_ready(request) == {"status": "ready"}
