"""Resolves which workflow a given Telegram user's next message belongs to.

This is the single place application code should call to answer "which
workflow is this user talking about right now" — handlers must never read
the conversation pointer or a workflow document directly to make that
decision themselves. No Claude/message-routing logic is implemented yet;
this milestone only wires /status and /cancel through it (see
src/handlers.py), but any future incoming-message handler must resolve
through here too.
"""

from __future__ import annotations

import logging

from ..workflow.errors import WorkflowNotFoundError
from ..workflow.manager import WorkflowManager
from ..workflow.models import WorkflowDocument
from .errors import ConversationNotFoundError, NoActiveWorkflowError, StaleWorkflowPointerError
from .manager import ConversationManager

logger = logging.getLogger(__name__)


def resolve_active_workflow(
    telegram_user_id: int,
    conversation_manager: ConversationManager,
    workflow_manager: WorkflowManager,
) -> WorkflowDocument:
    """Return the WorkflowDocument this user's active conversation points to.

    Raises NoActiveWorkflowError if there is no conversation record, or the
    conversation exists but has no active workflow pointer. Raises
    StaleWorkflowPointerError if the conversation points to a workflow_id
    that no longer exists in the workflow store (also covers a workflow
    record having gone missing entirely).

    Deliberately state-agnostic: the returned document may already be in a
    terminal state (see src/workflow/states.py's TERMINAL_STATES) — this
    milestone doesn't auto-clear pointers, so a resolved workflow can
    legitimately already be COMPLETED/REJECTED/SAVED_AS_DRAFT/FAILED.
    Callers that care (e.g. /cancel) check that themselves.
    """

    try:
        active_workflow_id = conversation_manager.load_active_workflow(telegram_user_id)
    except ConversationNotFoundError as exc:
        logger.info(
            "Workflow resolution failed: no conversation record telegram_user_id=%s",
            telegram_user_id,
        )
        raise NoActiveWorkflowError(
            f"no conversation record for telegram_user_id={telegram_user_id}"
        ) from exc

    if active_workflow_id is None:
        logger.info(
            "Workflow resolution failed: no active workflow telegram_user_id=%s", telegram_user_id
        )
        raise NoActiveWorkflowError(f"no active workflow for telegram_user_id={telegram_user_id}")

    try:
        document = workflow_manager.load_workflow(active_workflow_id)
    except WorkflowNotFoundError as exc:
        logger.error(
            "Workflow resolution failed: stale pointer telegram_user_id=%s workflow_id=%s",
            telegram_user_id,
            active_workflow_id,
        )
        raise StaleWorkflowPointerError(
            f"conversation for telegram_user_id={telegram_user_id} points to "
            f"missing workflow_id={active_workflow_id}"
        ) from exc

    logger.info(
        "Workflow resolved telegram_user_id=%s workflow_id=%s state=%s",
        telegram_user_id,
        document.workflow_id,
        document.state.value,
    )
    return document
