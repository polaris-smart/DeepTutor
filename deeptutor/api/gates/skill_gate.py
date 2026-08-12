"""Skill 掌握度门禁软校验（方案 B，悦学 fork 新增）。

在 assistant 完整回复生成后、落库前做一次软校验：
如果回复出现了"夸奖"或"换题"话术但没有报分，判定为 ⑧NEXT 结算被跳过，
追加一条 system 补救消息让模型补报分（不重跑整轮）。

设计要点（对齐 HS 方案 B 细则）：
- 只检查 assistant 角色的回复
- 触发：命中夸奖(PRAISE)或换题(NEXT)正则
- 校验：同回复是否命中分数(SCORE)正则
- 补救：追加 system 消息重新生成 1 次（最多 1 次），第 2 次放行 + 打日志
- 宿主端记录三个事实（example_skipped / max_hint_level / low_confidence），
  补救时喂回模型，掌握度不再依赖模型记忆。

仅当会话 settings 里启用了该门禁（gate_enabled=true）才生效，
不影响其它 skill 的正常对话。
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# 只在 assistant 回复上启用该门禁的 skill 标识（会话 settings.gate_skill）
GATE_SKILL = "khan-academy-shell"

# ---------------------------------------------------------------------------
# 正则（方案 B 3.1 / 3.2）
# ---------------------------------------------------------------------------

# 夸奖类
PRAISE = re.compile(
    r"(太棒了|做得(非常)?好|完全正确|很好|不错|完美|漂亮|厉害)"
)
# 换题类（匹配回复末尾的换题话术）
NEXT = re.compile(
    r"(下一(道|题|个)|下面(这|一)道|再来一道|挑战一道|进入下一个|我们来看.{0,6}题目\s*\d)"
)
# 分数模式：任一命中即算已报分
SCORE = re.compile(
    r"(\d{1,3}\s*分)|(合计\s*\d{1,3})|(达线|没达线|未达线)|(正确性\s*\d+)|(独立度\s*\d+)"
)

# 宿主端三个事实的学生消息命中词
SKIP_EXAMPLE_RE = re.compile(r"(例题.*(别|不用|跳过|不讲)|直接出题|跳例题)")
LOW_CONFIDENCE_RE = re.compile(r"(我猜的|不确定|大概是吧|瞎选|蒙的)")
HINT_LEVEL_RE = re.compile(r"(第\s*(\d)\s*级提示|这是第\s*(\d)\s*级)")

# 补救消息模板（方案 B 3.4）
REMEDY_TEMPLATE = (
    "你刚才夸奖了学生 / 出了下一题，但没有报本轮得分。\n"
    "按 khan-academy-shell 的 ⑧NEXT 规则重说一遍这段话：\n"
    "先报四维得分（正确性/独立度/说理/迁移）和合计分，说明达线或没达线，再继续。\n"
    "注意：本轮 example_skipped={example_skipped}，学生用到的最高提示级别={max_hint_level}。"
)


def _default_gate_state() -> dict[str, Any]:
    return {
        "example_skipped": False,
        "max_hint_level": 0,
        "low_confidence": False,
        "remedy_used": False,  # 本轮是否已补救过一次
    }


def init_gate_state() -> dict[str, Any]:
    """初始化宿主端记账状态。"""
    return _default_gate_state()


def update_gate_state(state: dict[str, Any], user_message: str) -> None:
    """根据学生消息更新宿主端三个事实。"""
    if not state:
        return
    if SKIP_EXAMPLE_RE.search(user_message):
        state["example_skipped"] = True
    if LOW_CONFIDENCE_RE.search(user_message):
        state["low_confidence"] = True


def needs_gate(session_settings: dict[str, Any] | None) -> bool:
    """当前会话是否启用该门禁。"""
    return bool(session_settings and session_settings.get("gate_skill") == GATE_SKILL)


def check_and_remedy(
    full_response: str,
    state: dict[str, Any],
) -> tuple[str, str | None]:
    """校验 assistant 回复，需要补救时返回 (是否补救, 补救消息)。

    Returns:
        (remedy_msg, new_user_msg)
        - remedy_msg: 需追加给模型的 system 补救消息（None = 放行）
        - new_user_msg: 补救后应重生成时传给学生的可见消息
    """
    # 命中夸奖或换题（换题只查回复末尾 200 字符，避免题目正文误伤）
    tail = full_response[-200:]
    hit_praise = bool(PRAISE.search(full_response))
    hit_next = bool(NEXT.search(tail))
    hit_score = bool(SCORE.search(full_response))

    violated = (hit_praise or hit_next) and not hit_score
    if not violated:
        return None, None

    # 违规，走补救（最多 1 次）
    if state.get("remedy_used"):
        logger.info("[skill_gate] gate_missing: 补救2次仍不报分，放行")
        return None, None

    state["remedy_used"] = True
    remedy = REMEDY_TEMPLATE.format(
        example_skipped=state.get("example_skipped", False),
        max_hint_level=state.get("max_hint_level", 0),
    )
    logger.info(
        "[skill_gate] ⑧NEXT 违规拦截：夸奖/换题无报分 → 补救（example_skipped=%s）",
        state.get("example_skipped"),
    )
    return remedy, remedy


__all__ = [
    "GATE_SKILL",
    "check_and_remedy",
    "init_gate_state",
    "needs_gate",
    "update_gate_state",
]
