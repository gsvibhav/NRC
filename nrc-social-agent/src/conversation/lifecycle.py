"""Reusable helper for the active-workflow pointer's terminal-state
lifecycle rule (see src/workflow/states.py's TERMINAL_STATES and
docs/WORKFLOW.md §9): once a workflow reaches a terminal state, the
Telegram user's active pointer should be cleared so a finished post never
blocks a new upload. Every code path that can land a workflow in a
terminal state — `/cancel`, a permanent AI failure in
src/ai/analysis_service.py or src/ai/clarification_service.py, the
clarification-question-limit-exhausted case — calls this one function
afterward, instead of each independently re-deriving the TERMINAL_STATES
check and its own error handling (Milestone 4B's "wire reusable automatic
clearing" requirement).

Deliberately a free function, not a method on WorkflowManager or
ConversationManager: clearing the pointer is a *cross-domain* rule (it
depends on workflow state but acts on the conversation record), and
neither manager depends on the other's package — keeping this coupling
here, one layer up, preserves that separation.
"""

from __future__ import annotations

import logging

from ..workflow.states import TERMINAL_STATES, WorkflowState
from .errors import ConversationError
from .manager import ConversationManager

logger = logging.getLogger(__name__)


def clear_pointer_if_terminal(
    *,
    telegram_user_id: int,
    workflow_state: WorkflowState,
    conversation_manager: ConversationManager,
) -> None:
    """No-op unless `workflow_state` is terminal. Never raises — a failure
    to clear the pointer is logged, not propagated, since the workflow's
    own state transition (the caller's real unit of work) has already
    succeeded by the time this runs."""

    if workflow_state not in TERMINAL_STATES:
        return

    try:
        conversation_manager.clear_active_workflow(telegram_user_id)
        logger.info(
            "Active workflow cleared (terminal state=%s) telegram_user_id=%s",
            workflow_state.value,
            telegram_user_id,
        )
    except ConversationError:
        logger.warning(
            "Failed to clear active workflow telegram_user_id=%s", telegram_user_id, exc_info=True
        )
