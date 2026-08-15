"""Pure post-processing for structurally annotated RAG retrieval nodes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from llama_index.core.schema import BaseNode


_PREVIEW_LENGTH = 200
_RELATED_LIMIT = 5

#: Match priority of a question node against a knowledge-point reference:
#: exact textbook-node bridge, then struct_path relation, then name overlap.
_NODE_ID_HIT = 3
_STRUCT_PATH_HIT = 2
_NAME_HIT = 1


def enrich_search_results(
    nodes: list["BaseNode"],
    kb_name: str,
    *,
    kp: Mapping[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Build student-facing structure and resource hints from retrieved nodes.

    ``nodes`` have already been retrieved from ``kb_name``.  The knowledge-base
    name is kept in this small, side-effect-free boundary so callers do not need
    a different interface when a later retriever supplies neighbouring nodes.
    Nodes from older indexes simply lack the doc-intel metadata and contribute
    no entries.

    ``kp`` is an optional knowledge-point reference (e.g. one entry of
    ``mastery_status``'s map) carrying ``textbook_node_id`` / ``struct_path`` /
    ``name``. When given, ``related_questions`` is matched node_id-first with
    name fallback: questions anchored on the same textbook-tree node rank
    first, then same-struct-path ones, then name-overlap ones. With no ``kp``
    (or for questions that match nothing) the behaviour is exactly the legacy
    one — first-come order, no filtering.
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
        textbook_node_id = _string(metadata.get("textbook_node_id"))
        if not (struct_path or textbook_node_id):
            # Chunks inherit the *document's* metadata, so per-block doc_intel
            # data rides in as a JSON list under di_block_meta (review round 2:
            # the node_id bridge must fire on production indexes). Recover this
            # chunk's own block entry — q_id match first, then text overlap.
            recovered = _recover_block_meta(metadata, node)
            if recovered is not None:
                struct_path = struct_path or _string(recovered.get("struct_path"))
                textbook_node_id = textbook_node_id or _string(
                    recovered.get("textbook_node_id")
                )

        # A structural location is only meaningful for doc-intel annotated
        # nodes. This also makes old, metadata-free indexes degrade to empties.
        if struct_path:
            location: dict[str, Any] = {
                "node_id": node_id,
                "file_name": _string(metadata.get("file_name")),
                "struct_path": struct_path,
                "preview": _preview(node),
            }
            if textbook_node_id:
                location["textbook_node_id"] = textbook_node_id
            locations.append(location)

        if metadata.get("is_question"):
            q_id = _string(metadata.get("q_id"))
            question_key = q_id or node_id
            if question_key and question_key not in seen_questions:
                seen_questions.add(question_key)
                question: dict[str, Any] = {
                    "node_id": node_id,
                    "q_id": q_id,
                    "q_type": _string(metadata.get("q_type")),
                    "struct_path": struct_path,
                    "preview": _preview(node),
                    "has_answer": bool(metadata.get("has_answer")),
                }
                if textbook_node_id:
                    question["textbook_node_id"] = textbook_node_id
                question["_kp_match"] = _kp_match_score(
                    kp, textbook_node_id, struct_path, _preview(node)
                )
                related_questions.append(question)

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

    if kp is not None:
        related_questions.sort(key=lambda q: q["_kp_match"], reverse=True)
    for question in related_questions[: _RELATED_LIMIT]:
        question.pop("_kp_match", None)

    return {
        "locations": locations,
        "related_questions": related_questions[: _RELATED_LIMIT],
        "related_images": related_images,
    }


def _recover_block_meta(
    metadata: Mapping[str, Any], node: Any
) -> Mapping[str, Any] | None:
    """Best-effort recover this chunk's doc_intel block entry.

    ``di_block_meta`` is the document-level list injected by the loader; a
    chunk whose text matches a block (or that carries a matching ``q_id``)
    gets that block's struct_path/textbook_node_id. Fail-open: any error or
    miss returns None.
    """
    import json as _json

    raw = _string(metadata.get("di_block_meta"))
    if not raw:
        return None
    try:
        blocks = _json.loads(raw)
    except Exception:
        return None
    if not isinstance(blocks, list):
        return None
    q_id = _string(metadata.get("q_id"))
    if q_id:
        for block in blocks:
            if isinstance(block, dict) and _string(block.get("q_id")) == q_id:
                return block
    text = _preview(node)
    if not text:
        return None
    best: tuple[int, Mapping[str, Any]] = (0, {})
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_text = _string(block.get("text") or block.get("preview"))
        if not block_text:
            continue
        head = block_text[:40]
        overlap = len(set(head) & set(text[:60]))
        if overlap > best[0]:
            best = (overlap, block)
    return best[1] if best[0] >= 12 else None


def _kp_match_score(
    kp: Mapping[str, Any] | None,
    textbook_node_id: str,
    struct_path: str,
    preview: str,
) -> int:
    """Rank a question node against a KP reference (0 = no match).

    node_id-first with name fallback: an exact ``textbook_node_id`` bridge
    beats a struct_path relation, which beats KP-name overlap in the question
    text or its structural path. A ``None`` kp matches nothing (score 0) so
    the legacy order is preserved.
    """
    if kp is None:
        return 0
    kp_node_id = _string(kp.get("textbook_node_id"))
    if kp_node_id and textbook_node_id == kp_node_id:
        return _NODE_ID_HIT
    kp_path = _string(kp.get("struct_path"))
    if kp_path and struct_path:
        if struct_path == kp_path or struct_path.startswith(kp_path + "/"):
            return _STRUCT_PATH_HIT
    kp_name = _string(kp.get("name"))
    if kp_name and (kp_name in preview or kp_name in struct_path):
        return _NAME_HIT
    return 0


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
