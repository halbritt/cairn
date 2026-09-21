"""Cairn helper for queuing native Hermes wake messages.

Provides safe message queueing that preserves unsubmitted composer drafts,
serializes behind busy turns without interrupting active execution, and
pins delivery to the expected Hermes native session ID.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


def queue_hermes_wake(
    plugin_ctx: Any,
    content: str,
    *,
    expected_session_id: str,
    role: str = "user",
    on_consumed: Callable[[str, str | None], None] | None = None,
) -> bool:
    """Queue a wake message into the active Hermes CLI session.

    Requires a nonempty expected_session_id to pin delivery to the exact intended
    session. Uses the typed queue_message seam when available on PluginContext.
    Refuses if queue capability is unavailable on older Hermes runtimes without
    silent unverified fallback.
    """
    if not expected_session_id or not isinstance(expected_session_id, str) or not expected_session_id.strip():
        raise ValueError("expected_session_id must be a nonempty string for Cairn wake")

    if hasattr(plugin_ctx, "queue_message"):
        qm = plugin_ctx.queue_message(
            content,
            expected_session_id=expected_session_id,
            role=role,
            source="cairn-wake",
            on_consumed=on_consumed,
        )
        return qm is not None

    logger.warning("queue_hermes_wake: plugin context lacks queue_message capability; refusing unverified fallback")
    return False
