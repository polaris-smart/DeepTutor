from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from deeptutor.knowledge.manager import KnowledgeBaseManager

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover - optional dependency in lightweight environments
    FastAPI = None
    TestClient = None


pytestmark = pytest.mark.skipif(
    FastAPI is None or TestClient is None,
    reason="fastapi not installed",
)

if FastAPI is not None and TestClient is not None:
    knowledge_router = importlib.import_module("deeptutor.api.routers.knowledge")
else:  # pragma: no cover - guarded by pytestmark
    knowledge_router = None


class _FakeKBManager:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.config: dict[str, dict] = {"knowledge_bases": {}}

    def _load_config(self) -> dict:
        return self.config

    def _save_config(self) -> None:
        return None

    def list_knowledge_bases(self) -> list[str]:
        return sorted(self.config["knowledge_bases"])

    def update_kb_status(self, name: str, status: str, progress: dict | None = None) -> None:
        entry = self.config["knowledge_bases"].setdefault(name, {"path": name})
        entry["status"] = status
        entry["progress"] = progress

    def get_default(self, *, available_names: list[str] | None = None) -> str | None:
        names = available_names if available_names is not None else self.list_knowledge_bases()
        return names[0] if names else None

    def get_info(
        self,
        name: str,
        *,
        refresh_config: bool = True,
        default_name: str | None = None,
    ) -> dict:
        entry = self.config["knowledge_bases"][name]
        return {
            "name": name,
            "group": entry.get("group", ""),
            "path": str(self.base_dir / name),
            "is_default": name == default_name,
            "statistics": {},
            "metadata": {"name": name},
            "status": entry.get("status", "ready"),
            "progress": entry.get("progress"),
        }


class _FakeInitializer:
    def __init__(self, kb_name: str, base_dir: str, **kwargs) -> None:
        self.raw_dir = Path(base_dir) / kb_name / "raw"
        self.progress_tracker = kwargs.get("progress_tracker")

    def create_directory_structure(self) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def _register_to_config(self) -> None:
        return None


def _build_app() -> FastAPI:
    if FastAPI is None or knowledge_router is None:  # pragma: no cover - guarded by pytestmark
        raise RuntimeError("fastapi is not installed")
    app = FastAPI()
    app.include_router(knowledge_router.router, prefix="/api/v1/knowledge")
    return app


def _upload() -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("files", ("test.txt", b"test", "text/plain"))]


@pytest.fixture
def api_client(monkeypatch, tmp_path: Path):
    manager = _FakeKBManager(tmp_path / "knowledge_bases")
    monkeypatch.setattr(knowledge_router, "get_kb_manager", lambda: manager)
    monkeypatch.setattr(knowledge_router, "_current_kb_base_dir", lambda: manager.base_dir)
    monkeypatch.setattr(knowledge_router, "KnowledgeBaseInitializer", _FakeInitializer)
    monkeypatch.setattr(knowledge_router, "list_visible_kb_access", lambda: [])

    async def _noop_init_task(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(knowledge_router, "run_initialization_task", _noop_init_task)
    with TestClient(_build_app()) as client:
        yield client, manager


def test_create_with_group_persists_group(api_client) -> None:
    client, manager = api_client

    response = client.post(
        "/api/v1/knowledge/create",
        data={"name": "test-kb-1", "group": "Group A"},
        files=_upload(),
    )

    assert response.status_code == 200
    assert manager.config["knowledge_bases"]["test-kb-1"]["group"] == "Group A"


def test_create_without_group_persists_empty_group(api_client) -> None:
    client, manager = api_client

    response = client.post(
        "/api/v1/knowledge/create",
        data={"name": "test-kb-1"},
        files=_upload(),
    )

    assert response.status_code == 200
    assert manager.config["knowledge_bases"]["test-kb-1"]["group"] == ""


def test_list_filters_by_group(api_client) -> None:
    client, manager = api_client
    manager.config["knowledge_bases"] = {
        "test-kb-1": {"path": "test-kb-1", "group": "Group A", "status": "ready"},
        "test-kb-2": {"path": "test-kb-2", "group": "", "status": "ready"},
    }

    response = client.get("/api/v1/knowledge/list", params={"group": "Group A"})
    uncategorized_response = client.get("/api/v1/knowledge/list", params={"group": ""})

    assert response.status_code == 200
    assert [(item["name"], item["group"]) for item in response.json()] == [
        ("test-kb-1", "Group A")
    ]
    assert uncategorized_response.status_code == 200
    assert [(item["name"], item["group"]) for item in uncategorized_response.json()] == [
        ("test-kb-2", "")
    ]


def test_old_config_without_group_is_uncategorized(tmp_path: Path) -> None:
    base_dir = tmp_path / "knowledge_bases"
    (base_dir / "test-kb-1").mkdir(parents=True)
    config_path = base_dir / "kb_config.json"
    config_path.write_text(
        json.dumps(
            {
                "knowledge_bases": {
                    "test-kb-1": {
                        "path": "test-kb-1",
                        "rag_provider": "llamaindex",
                        "status": "unknown",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    manager = KnowledgeBaseManager(base_dir)

    assert manager.get_info("test-kb-1")["group"] == ""
    assert "group" not in json.loads(config_path.read_text(encoding="utf-8"))["knowledge_bases"][
        "test-kb-1"
    ]
