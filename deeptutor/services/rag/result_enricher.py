"""Pure post-processing for structurally annotated RAG retrieval nodes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from llama_index.core.schema import BaseNode


_PREVIEW_LENGTH = 200
_RELATED_LIMIT = 5


def enrich_search_results(nodes: list["BaseNode"], kb_name: str) -> dict[str, list[dict[str, Any]]]:
    """Build student-facing structure and resource hints from retrieved nodes.

    ``nodes`` have already been retrieved from ``kb_name``.  The knowledge-base
    name is kept in this small, side-effect-free boundary so callers do not need
    a different interface when a later retriever supplies neighbouring nodes.
    Nodes from older indexes simply lack the doc-intel metadata and contribute
    no entries.
    """
    _ = kb_name
    locations: list[dict[str, Any]] = []
    related_questions: list[dict[str, Any]] = []
    related_images: list[dict[str, Any]] = []
    seen_questions: set[str] = set()
    seen_images: set[str] = set()

    for node in nodes or []:
        metadata = _metadata(node)
        struct_path = _string(metadata.get("struct_path"))
        node_id = _node_id(node)

        # A structural location is only meaningful for doc-intel annotated
        # nodes. This also makes old, metadata-free indexes degrade to empties.
        if struct_path:
            locations.append(
                {
                    "node_id": node_id,
                    "file_name": _string(metadata.get("file_name")),
                    "struct_path": struct_path,
                    "preview": _preview(node),
                }
            )

        if metadata.get("is_question") and len(related_questions) < _RELATED_LIMIT:
            q_id = _string(metadata.get("q_id"))
            question_key = q_id or node_id
            if question_key and question_key not in seen_questions:
                seen_questions.add(question_key)
                related_questions.append(
                    {
                        "node_id": node_id,
                        "q_id": q_id,
                        "q_type": _string(metadata.get("q_type")),
                        "preview": _preview(node),
                        "has_answer": bool(metadata.get("has_answer")),
                    }
                )

        if len(related_images) >= _RELATED_LIMIT:
            continue
        if not any(
            key in metadata for key in ("linked_images", "image_of", "image_path", "img_path")
        ):
            continue
        image_of = _string(metadata.get("image_of"))
        for image_path in _image_paths(node, metadata):
            if len(related_images) >= _RELATED_LIMIT:
                break
            if image_path in seen_images:
                continue
            seen_images.add(image_path)
            related_images.append(
                {
                    "node_id": node_id,
                    "image_path": image_path,
                    "image_of": image_of,
                }
            )

    return {
        "locations": locations,
        "related_questions": related_questions,
        "related_images": related_images,
    }


def _metadata(node: "BaseNode") -> Mapping[str, Any]:
    try:
        metadata = getattr(node, "metadata", None)
    except Exception:
        return {}
    return metadata if isinstance(metadata, Mapping) else {}


def _node_id(node: "BaseNode") -> str:
    for attribute in ("node_id", "id_"):
        try:
            value = getattr(node, attribute, None)
        except Exception:
            continue
        if value is not None and str(value):
            return str(value)
    return ""


def _preview(node: "BaseNode") -> str:
    try:
        get_content = getattr(node, "get_content", None)
        text = get_content() if callable(get_content) else getattr(node, "text", "")
    except Exception:
        return ""
    return _string(text)[:_PREVIEW_LENGTH]


def _image_paths(node: "BaseNode", metadata: Mapping[str, Any]) -> list[str]:
    paths = _as_string_list(metadata.get("linked_images"))
    for value in (
        metadata.get("image_path"),
        metadata.get("img_path"),
        _attribute(node, "image_path"),
    ):
        path = _string(value)
        if path:
            paths.append(path)
    return paths


def _attribute(node: "BaseNode", name: str) -> Any:
    try:
        return getattr(node, name, None)
    except Exception:
        return None


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, (list, tuple, set)):
        return []
    return [item for item in (_string(item) for item in value) if item]


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


__all__ = ["enrich_search_results"]
