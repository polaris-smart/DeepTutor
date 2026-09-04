"""目级 (level-4, type="mu") KP nodes — B2-a.

Covers the three deterministic mu signals (判据 1/2 + X.Y.Z numbering), the
栏目黑名单 exclusion (判据 3), the "挂在最近的节之下" parenting and the
unchanged node_id formula (判据 4). Real-sample regression runs separately
(样书验证, see B2A-验收.md); these fixtures stay synthetic and fast.
"""

from __future__ import annotations

import re

import pytest

from deeptutor.knowledge.doc_intel.structure import build_tree, stable_node_id


def _flatten(tree: dict, acc: list | None = None) -> list[dict]:
    acc = acc if acc is not None else []
    for child in tree.get("children", []):
        acc.append(child)
        _flatten(child, acc)
    return acc


def mus_nodes(nodes: list[dict]) -> list[dict]:
    return [n for n in nodes if n.get("type") == "mu"]


# ── 判据 1: text_level >= 3 → 目节点 ─────────────────────────────────────


def test_text_level_3_block_becomes_mu_node():
    blocks = [
        {"type": "text", "text": "第一单元 中国共产党的领导", "text_level": 1, "page_idx": 0},
        {"type": "text", "text": "第一课 社会主义从空想到科学", "text_level": 2, "page_idx": 0},
        {"type": "text", "text": "科学社会主义的理论与实践", "text_level": 3, "page_idx": 0},
        {"type": "text", "text": "空想社会主义之所以是空想，在于……", "page_idx": 0},
    ]
    tree, paths = build_tree(blocks, doc_id="doc-a")
    mus = mus_nodes(_flatten(tree))
    assert len(mus) == 1
    mu = mus[0]
    assert mu["level"] == 4
    assert mu["title"] == "科学社会主义的理论与实践"
    assert mu["struct_path"] == "第一单元 中国共产党的领导/第一课 社会主义从空想到科学/科学社会主义的理论与实践"
    # 判据 4: node_id 公式不变。
    assert mu["node_id"] == stable_node_id("doc-a", mu["struct_path"])
    # 目节点挂在最近的节之下 (此处无独立节，挂在课之下)。
    lesson = tree["children"][0]["children"][0]
    assert lesson["children"][0]["type"] == "mu"
    # 目下的正文块继承含目名的路径。
    assert paths[3].endswith("/科学社会主义的理论与实践")


# ── X.Y.Z 三段编号 → 目 (样书实锤: 人教A数学选必三) ──────────────────────


def test_sub_sub_numbering_becomes_mu_under_section():
    blocks = [
        {"type": "text", "text": "第六章 计数原理", "text_level": 1, "page_idx": 0},
        {"type": "text", "text": "6.2 排列与组合", "text_level": 2, "page_idx": 0},
        {"type": "text", "text": "6.2.1 排列", "text_level": 2, "page_idx": 0},
        {"type": "text", "text": "排列的定义……", "page_idx": 0},
    ]
    tree, paths = build_tree(blocks, doc_id="doc-b")
    mus = mus_nodes(_flatten(tree))
    assert [m["title"] for m in mus] == ["6.2.1 排列"]
    assert mus[0]["struct_path"] == "第六章 计数原理/6.2 排列与组合/6.2.1 排列"
    section = tree["children"][0]["children"][0]
    assert section["title"] == "6.2 排列与组合"
    assert "type" not in section  # 节不标 mu
    assert paths[3] == mus[0]["struct_path"]


def test_body_sentence_starting_with_numbers_is_not_mu():
    blocks = [
        {"type": "text", "text": "第六章 计数原理", "text_level": 1, "page_idx": 0},
        {"type": "text", "text": "6.2 排列与组合", "text_level": 2, "page_idx": 0},
        # 正文句：长且带句读，不是标题。
        {"type": "text", "text": "4.8.3节例4中推断吸烟与患肺癌是有关联的，能用回归模型建立", "page_idx": 0},
    ]
    tree, _ = build_tree(blocks, doc_id="doc-b")
    assert mus_nodes(_flatten(tree)) == []


# ── 判据 2: 加粗标记 + 去标记后 ≤20 字的目式短语 → 目节点 ────────────────


def test_bold_short_block_becomes_mu_node():
    blocks = [
        {"type": "text", "text": "第二单元 遵循逻辑思维规则", "text_level": 1, "page_idx": 0},
        {"type": "text", "text": "第四课 准确把握概念", "text_level": 2, "page_idx": 0},
        {"type": "text", "text": "**概念的内涵与外延**", "page_idx": 0},
        {"type": "text", "text": "概念的内涵是指概念所反映的事物的本质属性。", "page_idx": 0},
    ]
    tree, paths = build_tree(blocks, doc_id="doc-c")
    mus = mus_nodes(_flatten(tree))
    assert [m["title"] for m in mus] == ["概念的内涵与外延"]
    assert paths[3].endswith("/概念的内涵与外延")


@pytest.mark.parametrize(
    "text",
    [
        "**马克思主义中国化时代化是一个不断推进的历史进程，需要在实践中不断丰富和发展其理论内涵**",  # 去标记后 >20 字
        "**这个观点。**",  # 带句号，句子不是目
        "**Look and discuss**",  # 无 CJK，非中文目式短语
        "1.1 集合的概念",  # 编号节，归 level 3
    ],
)
def test_non_mu_shapes_stay_body_blocks(text: str):
    blocks = [
        {"type": "text", "text": "第一课 集合", "text_level": 2, "page_idx": 0},
        {"type": "text", "text": text, "page_idx": 0},
    ]
    tree, _ = build_tree(blocks, doc_id="doc-c")
    assert mus_nodes(_flatten(tree)) == []


# ── 判据 3: 栏目黑名单排除 ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "level"),
    [("探究与分享", 3), ("相关链接", 3), ("思考与讨论", 4), ("**探究与分享**", None)],
)
def test_column_blacklist_blocks_mu(text: str, level: int | None):
    block = {"type": "text", "text": text, "page_idx": 0}
    if level is not None:
        block["text_level"] = level
    blocks = [
        {"type": "text", "text": "第一课 把握世界的规律", "text_level": 2, "page_idx": 0},
        block,
        {"type": "text", "text": "正文。", "page_idx": 0},
    ]
    tree, _ = build_tree(blocks, doc_id="doc-d")
    flattened = _flatten(tree)
    assert mus_nodes(flattened) == []
    # 栏目名也不得冒充更高层级节点。
    assert all("探究" not in n["title"] and "相关链接" not in n["title"] for n in flattened)


# ── 判据 4: node_id 公式与去重 ───────────────────────────────────────────


def test_mu_nodes_reuse_same_struct_path_identity():
    blocks = [
        {"type": "text", "text": "第一课 概念", "text_level": 2, "page_idx": 0},
        {"type": "text", "text": "**内涵**", "page_idx": 0},
        {"type": "text", "text": "**外延**", "page_idx": 0},
    ]
    tree, _ = build_tree(blocks, doc_id="doc-e")
    mus = mus_nodes(_flatten(tree))
    assert [m["title"] for m in mus] == ["内涵", "外延"]
    assert all(re.fullmatch(r"[0-9a-f]{12}", m["node_id"]) for m in mus)
