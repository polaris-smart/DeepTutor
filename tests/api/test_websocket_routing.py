"""WebSocket routes must not inherit HTTP-only application dependencies."""

from fastapi.routing import APIWebSocketRoute

from deeptutor.api.main import app
from deeptutor.api.routers.auth import require_learning_surface



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


def test_websocket_routes_share_one_canonical_namespace() -> None:
    expected_paths = {
        "/ws",
        "/ws/books",
        "/ws/questions/mimic",
        "/ws/questions/generate",
        "/ws/questions/judge",
        "/ws/knowledge-bases/{kb_name}/progress",
        "/ws/mastery-paths",
        "/ws/partners/{partner_id}",
        "/ws/partner-groups/{group_id}",
    }
    websocket_routes = {
        path: route
        for path, route in _flatten_app_routes(app.routes)
        if isinstance(route, APIWebSocketRoute)
    }

    assert set(websocket_routes) == expected_paths
    for route in websocket_routes.values():
        assert all(
            dependency.call is not require_learning_surface
            for dependency in route.dependant.dependencies
        )
