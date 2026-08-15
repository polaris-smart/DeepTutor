"""result_enricher textbook-tree bridge (教研评审真问题3).

``enrich_search_results(..., kp=...)`` ranks ``related_questions`` node_id-first
with name fallback: questions anchored on the same textbook-tree node first,
then same-struct-path, then name overlap. With no ``kp`` the legacy order and
output shape are preserved, and old indexes without ``textbook_node_id``
metadata degrade to struct_path / name matching.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from deeptutor.services.rag.result_enricher import enrich_search_results


@dataclass
class StubNode:
    node_id: str
    text: str
    metadata: dict[str, Any] | None = None

    def get_content(self) -> str:
        return self.text


def _question(node_id: str, text: str, q_id: str, **meta: Any) -> StubNode:
    md = {"struct_path": "第3章/3.2 函数的极值", "is_question": True, "q_id": q_id}
    md.update(meta)
    return StubNode(node_id, text, md)


# ── node_id-first, name fallback ranking ─────────────────────────────────


def test_kp_ranks_node_id_first_then_struct_path_then_name() -> None:
    questions = [
        _question("q-name", "关于函数的极值的常见误区", "P-NAME", struct_path="第3章/3.3 函数的应用"),
        _question("q-node", "3.2 教材例题", "P-NODE", textbook_node_id="node12345678"),
        _question("q-other1", "集合运算", "P-OTHER1", struct_path="第1章/1.1 集合"),
        _question("q-other2", "向量运算", "P-OTHER2", struct_path="第2章/2.1 向量"),
        _question("q-other3", "概率初步", "P-OTHER3", struct_path="第5章/5.1 概率"),
        _question("q-path", "3.2 课后练习", "P-PATH", struct_path="第3章/3.2 函数的极值/拓展"),
    ]
    kp = {
        "textbook_node_id": "node12345678",
        "struct_path": "第3章/3.2 函数的极值",
        "name": "函数的极值",
    }
    enriched = enrich_search_results(questions, kb_name="数学", kp=kp)
    assert [q["q_id"] for q in enriched["related_questions"]] == [
        "P-NODE",  # exact textbook-node bridge (3)
        "P-PATH",  # struct_path under the KP's node (2)
        "P-NAME",  # name overlap only (1)
        "P-OTHER1",  # no match (0) — legacy order among ties
        "P-OTHER2",
    ]
    assert len(enriched["related_questions"]) == 5  # limit kept


def test_kp_name_fallback_when_node_id_unknown() -> None:
    # Old index: question nodes have no textbook_node_id metadata; the KP's
    # node id matches nothing, so matching falls back to the name.
    question = _question("q", "函数的极值 计算题", "P1")
    kp = {"textbook_node_id": "zzz999", "name": "函数的极值"}
    enriched = enrich_search_results([question], kb_name="数学", kp=kp)
    assert [q["q_id"] for q in enriched["related_questions"]] == ["P1"]


def test_kp_struct_path_relation_ranks_above_name() -> None:
    questions = [
        _question("q-name", "极值点判断", "P-NAME", struct_path="第3章/3.3 函数的应用"),  # name hit (1)
        _question("q-path", "极值相关练习", "P-PATH", struct_path="第3章/3.2 函数的极值/提高"),  # path (2)
    ]
    kp = {"struct_path": "第3章/3.2 函数的极值", "name": "函数的极值"}
    enriched = enrich_search_results(questions, kb_name="数学", kp=kp)
    assert [q["q_id"] for q in enriched["related_questions"]] == ["P-PATH", "P-NAME"]


# ── backward compatibility ───────────────────────────────────────────────


def test_kp_none_preserves_legacy_order_and_shape() -> None:
    questions = [
        _question("q1", "题一", "P1"),
        _question("q2", "题二", "P2"),
        _question("q3", "题三", "P3"),
        _question("q4", "题四", "P4"),
        _question("q5", "题五", "P5"),
        _question("q6", "题六", "P6"),
    ]
    enriched = enrich_search_results(questions, kb_name="数学")
    assert [q["q_id"] for q in enriched["related_questions"]] == ["P1", "P2", "P3", "P4", "P5"]
    # Internal match marker must never leak into the public payload.
    assert all("_kp_match" not in q for q in enriched["related_questions"])
    assert all("textbook_node_id" not in q for q in enriched["related_questions"])


def test_no_kp_match_keeps_legacy_order() -> None:
    # kp present but no question matches: identical order to the legacy path.
    questions = [_question("q1", "集合", "P1"), _question("q2", "向量", "P2")]
    kp = {"textbook_node_id": "nope", "name": "不存在的知识点"}
    enriched = enrich_search_results(questions, kb_name="数学", kp=kp)
    assert [q["q_id"] for q in enriched["related_questions"]] == ["P1", "P2"]


# ── bridge fields in the output ──────────────────────────────────────────


def test_related_questions_carry_bridge_fields_when_present() -> None:
    question = _question("q1", "题", "P1", textbook_node_id="abc123def456")
    enriched = enrich_search_results([question], kb_name="数学")
    entry = enriched["related_questions"][0]
    assert entry["struct_path"] == "第3章/3.2 函数的极值"
    assert entry["textbook_node_id"] == "abc123def456"


def test_locations_carry_textbook_node_id_when_present() -> None:
    node = StubNode(
        "n1",
        "正文",
        {"file_name": "数学必修一.pdf", "struct_path": "第3章/3.2 函数的极值",
         "textbook_node_id": "abc123def456"},
    )
    enriched = enrich_search_results([node], kb_name="数学")
    assert enriched["locations"][0]["textbook_node_id"] == "abc123def456"


def test_locations_omit_textbook_node_id_for_old_indexes() -> None:
    # Legacy metadata-free of the bridge field: the entry shape is unchanged.
    node = StubNode(
        "n1", "正文", {"file_name": "数学必修一.pdf", "struct_path": "第3章/3.2 函数的极值"}
    )
    enriched = enrich_search_results([node], kb_name="数学")
    assert "textbook_node_id" not in enriched["locations"][0]
