"""B2-b【1】KnowledgePoint 扩字段：旧 state 兼容 + 新字段序列化往返."""

from __future__ import annotations

import json

from deeptutor.learning.models import (
    KnowledgePoint,
    KnowledgeType,
    LearningModule,
    LearningProgress,
)


def _old_kp_dict() -> dict:
    """旧形态 state_json 里的 KP：没有 B2-b 三个新字段，也缺 visualizers/meta."""
    return {
        "id": "bk1_ch0_kp0",
        "name": "集合的概念",
        "type": "concept",
        "module_id": "bk1_ch0",
        "struct_path": "必修一/第一章 集合/1.1 集合的概念",
        "textbook_node_id": "a1b2c3d4e5f6",
    }


def _kp() -> KnowledgePoint:
    return KnowledgePoint(
        id="bk1_ch0_kp0",
        name="集合的概念",
        type=KnowledgeType("concept"),
        module_id="bk1_ch0",
        prerequisite_ids=["bk1_ch0_kp1"],
        curriculum_level="理解",
        page_span=[12, 15],
    )


def test_old_form_dict_loads_with_defaults() -> None:
    kp = KnowledgePoint.model_validate(_old_kp_dict())
    assert kp.prerequisite_ids == []
    assert kp.curriculum_level == ""
    assert kp.page_span is None
    assert kp.visualizers == []
    assert kp.meta == {}


def test_new_fields_serialize_roundtrip() -> None:
    kp = _kp()
    data = json.loads(kp.model_dump_json())
    restored = KnowledgePoint.model_validate(data)
    assert restored == kp
    assert restored.prerequisite_ids == ["bk1_ch0_kp1"]
    assert restored.curriculum_level == "理解"
    assert restored.page_span == [12, 15]


def test_partial_new_fields_fall_back_to_defaults() -> None:
    kp = KnowledgePoint.model_validate({**_old_kp_dict(), "curriculum_level": "掌握"})
    assert kp.curriculum_level == "掌握"
    assert kp.prerequisite_ids == []
    assert kp.page_span is None


def test_old_progress_state_json_still_deserializes() -> None:
    """整份旧 state_json（模块+KP 无新字段）读入不炸，新字段兜底默认值。"""
    old_state = {
        "book_id": "bk1",
        "modules": [
            {
                "id": "bk1_ch0",
                "name": "第一章 集合",
                "order": 0,
                "pass_threshold": 0.7,
                "knowledge_points": [_old_kp_dict()],
            }
        ],
        "current_stage": "diagnostic",
        "mastery_levels": {},
        "qualitative_mastery": {},
    }
    progress = LearningProgress.model_validate(old_state)
    kp = progress.modules[0].knowledge_points[0]
    assert isinstance(kp, KnowledgePoint)
    assert kp.prerequisite_ids == []
    assert kp.curriculum_level == ""
    assert kp.page_span is None


def test_module_knowledge_points_default_keeps_new_fields_on_roundtrip() -> None:
    module = LearningModule(id="m1", name="M1", order=0, knowledge_points=[_kp()])
    restored = LearningModule.model_validate(module.model_dump(mode="json"))
    assert restored.knowledge_points[0].page_span == [12, 15]
