"""Shared failure handling for the ask-hint generators.

Reading and mastery ask-hints fail the same way: the LLM call sits behind a
short timeout on a request path, an empty hint is a real answer rather than an
error, and re-paying a failed call within the next minute buys nothing — the
learner is still on the same screen. This module holds the one constant, the
failure reasons the responses carry, the 60-second negative cache both
services front their generator with, and the task-profile probe both use to
skip the wait entirely when no task model was ever configured.
"""

from __future__ import annotations

import logging
import time
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

# The ask-hint generators cap their LLM call with this, so a hung provider
# costs the request at most this long.
LLM_TIMEOUT_SECONDS = 12.0

# Empty results get a short TTL instead of the positive cache's half hour:
# a failure is worth not repeating within a minute, but the next minute is
# allowed to try again.
NEGATIVE_TTL_SECONDS = 60.0

REASON_LLM_TIMEOUT = "llm_timeout"
REASON_LLM_UNAVAILABLE = "llm_unavailable"
REASON_TASK_PROFILE_NOT_CONFIGURED = "task_profile_not_configured"

K = TypeVar("K")
V = TypeVar("V")


class NegativeResultCache(Generic[K, V]):
    """A bounded TTL map for empty results.

    Mirrors the recall/remember/clear surface the positive cache exposes so a
    service can front both with the same fixture hygiene. Entries expire by
    wall clock; the cap evicts oldest-first like the single-flight cache does.
    """

    def __init__(self, *, limit: int, ttl_seconds: float) -> None:
        if limit < 1:
            raise ValueError("limit must be positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.limit = limit
        self.ttl_seconds = ttl_seconds
        self._entries: dict[K, tuple[V, float]] = {}

    def recall(self, key: K) -> V | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        value, stored_at = entry
        if time.time() - stored_at > self.ttl_seconds:
            self._entries.pop(key, None)
            return None
        return value

    def remember(self, key: K, value: V) -> None:
        self._entries[key] = (value, time.time())
        overflow = len(self._entries) - self.limit
        if overflow > 0:
            for stale in list(self._entries)[:overflow]:
                self._entries.pop(stale, None)

    def clear(self) -> None:
        self._entries.clear()


def task_profile_configured(kind: str) -> bool:
    """Whether *kind* has a task profile with a usable model configured.

    Mirrors what :func:`task_llm_scope` checks before it would install the
    task model, so a generator can skip the whole call — timeout included —
    when the profile was never set up. Unreadable settings count as
    unconfigured: inheriting the global model was the old behaviour, but an
    ask-hint is a nicety and an empty one is a real answer.
    """
    from deeptutor.services.config.model_catalog import get_model_catalog_service
    from deeptutor.services.model_selection.tasks import (
        catalog_for_task,
        task_service_configured,
    )

    try:
        catalog = catalog_for_task(get_model_catalog_service().load(), kind)
    except Exception:
        logger.debug("task catalog unreadable for %s", kind, exc_info=True)
        return False
    return task_service_configured(catalog)


__all__ = [
    "LLM_TIMEOUT_SECONDS",
    "NEGATIVE_TTL_SECONDS",
    "NegativeResultCache",
    "REASON_LLM_TIMEOUT",
    "REASON_LLM_UNAVAILABLE",
    "REASON_TASK_PROFILE_NOT_CONFIGURED",
    "task_profile_configured",
]
