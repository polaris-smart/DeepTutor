"""B2-b【4】kp_mapper.map_question_to_kp：题目关键词 → KP（精确/模糊/unmapped）."""

from __future__ import annotations

from deeptutor.learning.kp_mapper import map_question_to_kp
from deeptutor.learning.models import KnowledgePoint, KnowledgeType

KPS = [
    {
        "id": "bk1_kp0",
        "name": "集合的概念",
        "textbook_node_id": "aa11bb22cc33",
    },
    {"id": "bk1_kp1", "name": "函数的单调性", "textbook_node_id": "dd44ee55ff66"},
    # 无教材桥（import 机械态常见）：textbook_node_id 缺省 → 返回空串。
    {"id": "bk1_kp2", "name": "充分必要条件"},
]


def test_exact_hit_wins() -> None:
    result = map_question_to_kp("集合的概念", KPS)
    assert result is not None
    assert result["match_type"] == "exact"
    assert result["kp_id"] == "bk1_kp0"
    assert result["textbook_node_id"] == "aa11bb22cc33"
    assert result["score"] == 1.0


def test_fuzzy_fallback_above_threshold() -> None:
    result = map_question_to_kp("集合的概念辨析", KPS)
    assert result is not None
    assert result["match_type"] == "fuzzy"
    assert result["kp_id"] == "bk1_kp0"
    assert result["score"] >= 0.6


def test_unmapped_returns_none() -> None:
    result = map_question_to_kp("数列的极限与洛必达法则", KPS)
    assert result is None


def test_accepts_knowledge_point_models() -> None:
    kp = KnowledgePoint(
        id="bk1_kp3",
        name="指数函数的图象与性质",
        type=KnowledgeType("concept"),
        module_id="bk1_m1",
        textbook_node_id="ff778899aabb",
    )
    result = map_question_to_kp("指数函数的图象与性质", [kp])
    assert result is not None
    assert result["match_type"] == "exact"
    assert result["kp_id"] == "bk1_kp3"
    assert result["textbook_node_id"] == "ff778899aabb"


def test_empty_keyword_returns_none() -> None:
    assert map_question_to_kp("   ", KPS) is None
    assert map_question_to_kp("", KPS) is None
