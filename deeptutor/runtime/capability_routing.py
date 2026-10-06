"""Pre-execution capability routing for explicit turn requests."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True)
class CapabilityRoute:
    requested_capability: str
    capability: str
    confidence: float
    strategy: str
    reason: str

    def as_metadata(self) -> dict[str, Any]:
        return {
            "requested_capability": self.requested_capability,
            "capability": self.capability,
            "confidence": self.confidence,
            "strategy": self.strategy,
            "reason": self.reason,
            "auto_routed": self.auto_routed,
        }

    @property
    def auto_routed(self) -> bool:
        return self.capability != self.requested_capability


# Deliberately narrow: an ambiguous mention of practice, a quiz, or an exam
# stays in chat. Only a request to generate a reusable question set routes to
# deep_question. Interactive requests such as "quiz me" stay in chat because
# deep_question is a static generator, not a turn-by-turn quiz runner.
_QUIZ_PATTERNS = (
    re.compile(
        r"\b(?:generate|create|make|write|give me|produce)\b[^.!?\n]{0,80}"
        r"\bquiz questions?\b",
        re.IGNORECASE,
    ),
    re.compile(r"(?:给我出|帮我出|生成|创建)[^.!?\n]{0,20}(?:测验|考试|测试)题?"),
)

_QUIZ_REASON = "The user explicitly asked to generate a quiz question set."


def route_explicit_quiz_request(
    content: Any,
    requested_capability: Any,
    *,
    enabled: bool,
    workspace_mode: Any = "",
) -> CapabilityRoute | None:
    """Return a rule-based route before turn creation, or None to keep chat.

    The requested capability is retained even when routing does not occur so
    observability can distinguish an explicit chat selection from a missing one.

    A conversation held inside a workspace is never routed. A workspace is a
    standing commitment — a mastery path to advance, a document to stay in —
    and only some actions carry it: routing a mastery turn to the static quiz
    generator would drop the tutor loop, the objective map and the mastery
    gate, leaving the learner being taught from their materials while their
    path stands still. Learners in a workspace can still pick another action
    themselves; what they cannot do is have it picked for them.
    """
    requested = str(requested_capability or "chat")
    if not enabled or requested != "chat":
        return None
    if str(workspace_mode or "").strip():
        return None

    message = str(content or "").strip()
    if not message or not any(pattern.search(message) for pattern in _QUIZ_PATTERNS):
        return None

    return CapabilityRoute(
        requested_capability=requested,
        capability="deep_question",
        confidence=0.96,
        strategy="rule",
        reason=_QUIZ_REASON,
    )


# Visualization asks are likewise narrow: only an explicit request for a
# drawn/rendered artifact lifts the turn into the visualize capability, whose
# loop owns the submit_visualization tool and the canvas envelope. Unlike the
# quiz route this one is deliberately NOT gated on capability_routing_enabled:
# the canvas is the only channel that renders these asks, and the model already
# attempts visualization unprompted (N4 实锤) — a disabled flag would silently
# turn every "画二次函数图像" back into a bare code block. Learning accounts
# stay protected: the route refuses unless their policy admits visualize,
# mirroring apply_learning_policy which runs right after routing.
_VISUALIZE_PATTERNS = (
    re.compile(
        r"\b(?:plot|draw|visuali[sz]e)\b[^.!?\n]{0,40}"
        r"\b(?:graph|chart|diagram|function|curve|animation|figure)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:画|绘制|绘出|生成|输出)[^.!?\n]{0,24}"
        r"(?:函数图像|图像|图象|图表|曲线|示意图|流程图|思维导图|概念图"
        r"|动画|可视化|柱状图|折线图|饼图|统计图)"
    ),
)

_VISUALIZE_REASON = "The user explicitly asked for a drawn or rendered visualization."


def route_explicit_visualize_request(
    content: Any,
    requested_capability: Any,
    *,
    workspace_mode: Any = "",
) -> CapabilityRoute | None:
    """Return a rule-based visualize route before turn creation, or None.

    Same guard rails as the quiz route (explicit ask only, chat requests only,
    never inside a workspace) plus a learning-policy check so a learner whose
    policy withholds visualize keeps the plain-chat surface.
    """
    requested = str(requested_capability or "chat")
    if requested != "chat":
        return None
    if str(workspace_mode or "").strip():
        return None
    try:
        from deeptutor.multi_user.learning_access import current_learning_policy

        policy = current_learning_policy()
    except Exception:
        policy = None
    if policy is not None:
        allowed = {str(item) for item in (policy.get("allowed_capabilities") or [])}
        if "visualize" not in allowed:
            return None
    message = str(content or "").strip()
    if not message or not any(pattern.search(message) for pattern in _VISUALIZE_PATTERNS):
        return None

    return CapabilityRoute(
        requested_capability=requested,
        capability="visualize",
        confidence=0.9,
        strategy="rule",
        reason=_VISUALIZE_REASON,
    )


__all__ = [
    "CapabilityRoute",
    "route_explicit_quiz_request",
    "route_explicit_visualize_request",
]
