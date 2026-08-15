"""Stable textbook-tree node ids (教研评审真问题3 bridge).

Every tree node carries a stable ``node_id = sha1(doc_id|struct_path)[:12]``
plus its ``struct_path``. Same (doc_id, struct_path) → same id (re-index
stable); different doc_id → different ids. ``enrich`` stamps each block with
the containing tree node's id (``textbook_node_id``) so question chunks can
bridge back to the node by id instead of fuzzy title matching.
"""

from __future__ import annotations

import re

import pytest

from deeptutor.knowledge.doc_intel import enrich
from deeptutor.knowledge.doc_intel.structure import build_tree, stable_node_id

# unit(1) → lesson(2) → section(3), with a body block under the deepest node.
NESTED_BLOCKS = [
    {"type": "text", "text": "第1单元 集合", "text_level": 1, "page_idx": 0},
    {"type": "text", "text": "第1课 集合的概念", "text_level": 2, "page_idx": 0},
    {"type": "text", "text": "1.1 集合的表示", "text_level": 3, "page_idx": 0},
    {"type": "text", "text": "集合是现代数学的基础语言。", "page_idx": 0},
    {"type": "text", "text": "第2单元 函数", "text_level": 1, "page_idx": 2},
    {"type": "text", "text": "第1课 函数的概念", "text_level": 2, "page_idx": 2},
]


def _flatten(tree: dict, acc: list | None = None) -> list[dict]:
    acc = acc if acc is not None else []
    for child in tree.get("children", []):
        acc.append(child)
        _flatten(child, acc)
    return acc


# ── stable_node_id ───────────────────────────────────────────────────────


def test_stable_node_id_format():
    nid = stable_node_id("doc-a", "第1单元 集合/第1课 集合的概念")
    assert re.fullmatch(r"[0-9a-f]{12}", nid)


def test_stable_node_id_deterministic_and_doc_scoped():
    path = "第1单元 集合/第1课 集合的概念"
    assert stable_node_id("doc-a", path) == stable_node_id("doc-a", path)
    # Same path in a different document gets a different id.
    assert stable_node_id("doc-a", path) != stable_node_id("doc-b", path)


# ── build_tree node ids ──────────────────────────────────────────────────


def test_build_tree_nodes_carry_struct_path_and_node_id():
    tree, _ = build_tree(NESTED_BLOCKS, doc_id="doc-a")
    assert tree is not None
    nodes = _flatten(tree)
    assert len(nodes) == 5  # 第1单元→第1课→1.1, 第2单元→第1课
    assert all(isinstance(n.get("node_id"), str) and n["node_id"] for n in nodes)
    assert all(isinstance(n.get("struct_path"), str) and n["struct_path"] for n in nodes)
    # struct_path is the "/"-joined heading chain.
    by_title = {n["title"]: n for n in nodes}
    assert by_title["第1单元 集合"]["struct_path"] == "第1单元 集合"
    assert by_title["第1课 集合的概念"]["struct_path"] == "第1单元 集合/第1课 集合的概念"
    assert by_title["1.1 集合的表示"]["struct_path"] == (
        "第1单元 集合/第1课 集合的概念/1.1 集合的表示"
    )


def test_build_tree_same_input_same_ids_across_calls():
    tree_a, _ = build_tree(NESTED_BLOCKS, doc_id="doc-a")
    tree_b, _ = build_tree(NESTED_BLOCKS, doc_id="doc-a")
    assert _flatten(tree_a) == _flatten(tree_b)


def test_build_tree_different_docs_different_ids():
    tree_a, _ = build_tree(NESTED_BLOCKS, doc_id="doc-a")
    tree_b, _ = build_tree(NESTED_BLOCKS, doc_id="doc-b")
    ids_a = {n["node_id"] for n in _flatten(tree_a)}
    ids_b = {n["node_id"] for n in _flatten(tree_b)}
    assert ids_a and ids_a.isdisjoint(ids_b)


def test_build_tree_node_id_matches_helper():
    tree, _ = build_tree(NESTED_BLOCKS, doc_id="doc-a")
    nodes = _flatten(tree)
    for node in nodes:
        assert node["node_id"] == stable_node_id("doc-a", node["struct_path"])


# ── enrich block_meta bridge ─────────────────────────────────────────────


def test_enrich_blocks_get_textbook_node_id_of_containing_node():
    r = enrich(NESTED_BLOCKS, "", "苏教必修1.pdf", doc_id="doc-a")
    tree = r["tree"]
    assert tree is not None
    section = tree["children"][0]["children"][0]["children"][0]
    assert section["title"] == "1.1 集合的表示"
    body_idx = 3  # the body block under 1.1
    md = r["block_meta"][body_idx]
    assert md["struct_path"] == section["struct_path"]
    assert md["textbook_node_id"] == section["node_id"]


def test_enrich_block_under_practice_header_keeps_section_node_id():
    # A "练习" header is demoted to body (page furniture), so the following
    # exercise block keeps the path of the containing section — and bridges
    # to that section's node id.
    blocks = NESTED_BLOCKS[:4] + [
        {"type": "text", "text": "练习", "text_level": 2, "page_idx": 1},
        {"type": "text", "text": "1. 用∈或∉填空", "page_idx": 1},
    ]
    r = enrich(blocks, "", "苏教必修1.pdf", doc_id="doc-a")
    tree = r["tree"]
    section = tree["children"][0]["children"][0]["children"][0]
    exercise_md = r["block_meta"][5]
    assert exercise_md["struct_path"] == "第1单元 集合/第1课 集合的概念/1.1 集合的表示"
    assert exercise_md["textbook_node_id"] == section["node_id"]


def test_node_id_for_path_prefers_nearest_ancestor():
    from deeptutor.knowledge.doc_intel.enrich import _node_id_for_path

    node_ids = {
        "第1单元 集合": "aaa111111111",
        "第1单元 集合/第1课 集合的概念": "bbb222222222",
    }
    # Exact match wins.
    assert _node_id_for_path(node_ids, "第1单元 集合/第1课 集合的概念") == "bbb222222222"
    # A path deeper than any tree node falls back to the nearest ancestor.
    assert _node_id_for_path(node_ids, "第1单元 集合/第1课 集合的概念/拓展阅读") == "bbb222222222"
    # No relation → empty (block bridges to nothing, e.g. front matter).
    assert _node_id_for_path(node_ids, "第2单元 函数") == ""


def test_enrich_without_doc_id_still_works():
    # Backward compat: doc_id defaults to "" — enrich never regresses.
    r = enrich(NESTED_BLOCKS, "", "苏教必修1.pdf")
    assert r["tree"] is not None
    assert all(isinstance(m, dict) for m in r["block_meta"])


# ── roundtrip through the tree payload (what document_loader persists) ───


def test_tree_dict_roundtrips_with_node_ids():
    import json

    tree, _ = build_tree(NESTED_BLOCKS, doc_id="doc-a")
    dumped = json.dumps(tree, ensure_ascii=False)
    restored = json.loads(dumped)
    assert _flatten(restored) == _flatten(tree)
    for node in _flatten(restored):
        assert "node_id" in node and "struct_path" in node


def test_pytest_importable_smoke():
    # Guard against accidental import breakage in the doc_intel package.
    from deeptutor.knowledge.doc_intel import enrich as _enrich

    assert callable(_enrich)


# ── legacy node shape preserved ──────────────────────────────────────────


def test_legacy_fields_unchanged():
    tree, _ = build_tree(NESTED_BLOCKS, doc_id="doc-a")
    nodes = _flatten(tree)
    assert all({"title", "level", "children"} <= set(n.keys()) for n in nodes)
