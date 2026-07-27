"""Top-level orchestration for the Publication Execution Layer
(Milestone 9).

Converts an already-`READY_FOR_PUBLISHING` publication package into a
runtime execution record — and nothing else. Never mutates the package,
never rewrites content, never calls Claude, never calls any platform API.
This service makes zero Claude calls and zero platform calls, full stop.

**Trigger.** Chained automatically right after publication preparation
succeeds (see handlers.py's `_run_publication_preparation_and_reply()`),
using the workflow_id already in hand from that same request. It is also
resumable via `/retry`'s `"publication_execution"` branch and via a plain
restart (a crash between "package persisted" and "execution created"
leaves `workflow.publication.status == "READY_FOR_PUBLISHING"` with no
execution record yet — `create_execution()` is safely re-driven from
that same durable signal, never regenerating or touching the package).

**Package resolution never trusts the cached workflow reference alone.**
`workflow.publication` (the lightweight reference Milestone 8 attaches)
is used only to *locate* the authoritative package
(`workflow_id`/`output_id`) — the actual `PublicationPackage` is always
re-loaded from `PublicationManager` and its own `status`/`channel`/
`publication_id` are what's actually checked and used. Execution is never
created from a package that isn't independently confirmed
`READY_FOR_PUBLISHING`.

**Pointer-clearing is deliberately parameterized, not owned outright.**
Milestone 8 established that the active conversation pointer should stay
alive until the *whole* approve -> prepare chain is finally resolved, so
`/retry`/`/status` can keep finding the workflow through the ordinary
active-pointer path. Milestone 9 extends that chain by one more link:
`create_execution()` accepts `clear_pointer_on_success` (default `True`
for a standalone call, e.g. via `/retry`) so the *caller* — specifically
`PublicationPreparationService`, which now also accepts and forwards a
`clear_pointer_on_success` flag — can suppress pointer-clearing at the
publication-preparation step and let this, the last link, clear it
instead. See PublicationPreparationService's own docstring for the
forwarding side of this.

**Failure taxonomy** — the same shape as src/publication/errors.py, for
the same reason (no Claude call to base a retryable/permanent split on):
`ExecutionPermanentError` subclasses (package not ready, package not
found, unsupported channel, failed validation) are specific, enumerated,
non-retryable conditions — the pointer is left untouched (not cleared),
mirroring publication preparation's own permanent-failure precedent,
since the underlying package and workflow remain completely valid.
Everything else under the generic `ExecutionError` base is retryable by
elimination — `metadata["pending_retry"] = "publication_execution"` is
set, and the pointer is likewise left untouched.

**Idempotency and concurrent creation.** `ExecutionManager.find_existing()`
is checked first — if a record already exists for this exact
publication_id, it is reused verbatim, never rebuilt. If two concurrent
calls both pass that check and race on the conditional create, the loser
gets `ExecutionConcurrentModificationError`; this service catches that
specific case and re-reads `find_existing()` one more time to reuse the
winner's record — "2 requests -> 1 execution," per this milestone's own
requirement — rather than surfacing the race as a user-facing failure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..conversation.lifecycle import clear_pointer_if_terminal
from ..conversation.manager import ConversationManager
from ..publication.manager import PublicationManager
from ..publication.models import PublicationStatus
from ..workflow.errors import WorkflowError
from ..workflow.manager import WorkflowManager
from ..workflow.states import WorkflowState
from .errors import (
    ExecutionConcurrentModificationError,
    ExecutionError,
    ExecutionPermanentError,
    PublicationPackageNotFoundForExecutionError,
    PublicationPackageNotReadyForExecutionError,
)
from .manager import ExecutionManager
from .models import resolve_publisher
from .validation import validate_execution

logger = logging.getLogger(__name__)

_RETRY_PUBLICATION_EXECUTION = "publication_execution"


@dataclass(frozen=True)
class ExecutionOutcome:
    workflow_id: str
    execution_id: str
    publication_id: str
    channel: str
    publisher: str
    status: str
    attempt: int


class ExecutionService:
    def __init__(
        self,
        *,
        workflow_manager: WorkflowManager,
        publication_manager: PublicationManager,
        execution_manager: ExecutionManager,
        conversation_manager: ConversationManager,
    ) -> None:
        self._workflow_manager = workflow_manager
        self._publication_manager = publication_manager
        self._execution_manager = execution_manager
        self._conversation_manager = conversation_manager

    async def create_execution(
        self, *, workflow_id: str, telegram_user_id: int, clear_pointer_on_success: bool = True
    ) -> ExecutionOutcome:
        """Create (or reuse) the execution record for `workflow_id`'s
        prepared publication package. Raises an ExecutionError subclass
        on any expected failure. Zero Claude calls, zero platform calls,
        on every path, including failure paths."""

        logger.info("Execution creation requested workflow_id=%s telegram_user_id=%s", workflow_id, telegram_user_id)

        try:
            workflow = self._workflow_manager.load_workflow(workflow_id)
        except WorkflowError as exc:
            raise ExecutionError(f"failed to load workflow_id={workflow_id}: {exc}") from exc

        publication_reference = workflow.publication or {}
        output_id = publication_reference.get("output_id")
        publication_id = publication_reference.get("publication_id")
        if not output_id or not publication_id or publication_reference.get("status") != "READY_FOR_PUBLISHING":
            raise PublicationPackageNotReadyForExecutionError(
                f"workflow_id={workflow_id} has no READY_FOR_PUBLISHING publication reference"
            )

        existing = self._execution_manager.find_existing(publication_id)
        if existing is not None:
            logger.info(
                "Duplicate execution creation skipped workflow_id=%s publication_id=%s (already exists)",
                workflow_id, publication_id,
            )
            self._finish(workflow_id, telegram_user_id, clear_pointer_on_success)
            return self._outcome(existing)

        try:
            package = self._publication_manager.find_existing(workflow_id, output_id)
            if package is None or package.status is not PublicationStatus.READY_FOR_PUBLISHING:
                raise PublicationPackageNotFoundForExecutionError(
                    f"workflow_id={workflow_id} output_id={output_id}: no READY_FOR_PUBLISHING package found"
                )
            if package.publication_id != publication_id:
                raise PublicationPackageNotFoundForExecutionError(
                    f"workflow_id={workflow_id}: loaded package publication_id does not match the workflow's own reference"
                )

            publisher = resolve_publisher(package.channel)
        except ExecutionPermanentError as exc:
            logger.error("Execution creation permanent failure workflow_id=%s error=%s", workflow_id, exc)
            self._clear_pending_retry(workflow_id)
            raise

        try:
            document = self._execution_manager.create_execution(
                publication_id=publication_id, workflow_id=workflow_id, channel=package.channel, publisher=publisher,
            )
            validate_execution(document)
        except ExecutionConcurrentModificationError:
            # The common, expected race: another concurrent call already
            # won the conditional create. Reconcile by reusing its
            # already-persisted record — never surfaced as a failure.
            reconciled = self._execution_manager.find_existing(publication_id)
            if reconciled is None:
                raise
            logger.info(
                "Execution creation reconciled a concurrent-creation race workflow_id=%s publication_id=%s",
                workflow_id, publication_id,
            )
            self._finish(workflow_id, telegram_user_id, clear_pointer_on_success)
            return self._outcome(reconciled)
        except ExecutionError as exc:
            logger.warning("Execution creation retryable failure workflow_id=%s error=%s", workflow_id, exc)
            self._set_pending_retry(workflow_id)
            raise

        logger.info(
            "Execution record persisted workflow_id=%s publication_id=%s execution_id=%s",
            workflow_id, publication_id, document.execution_id,
        )
        self._finish(workflow_id, telegram_user_id, clear_pointer_on_success)
        return self._outcome(document)

    def _finish(self, workflow_id: str, telegram_user_id: int, clear_pointer_on_success: bool) -> None:
        self._clear_pending_retry(workflow_id)
        if not clear_pointer_on_success:
            return
        clear_pointer_if_terminal(
            telegram_user_id=telegram_user_id, workflow_state=WorkflowState.COMPLETED,
            conversation_manager=self._conversation_manager,
        )

    def _set_pending_retry(self, workflow_id: str) -> None:
        try:
            self._workflow_manager.update_metadata(workflow_id, {"pending_retry": _RETRY_PUBLICATION_EXECUTION})
        except WorkflowError:
            logger.error("Failed to set pending_retry workflow_id=%s", workflow_id, exc_info=True)

    def _clear_pending_retry(self, workflow_id: str) -> None:
        try:
            self._workflow_manager.update_metadata(workflow_id, {"pending_retry": None})
        except WorkflowError:
            logger.error("Failed to clear pending_retry workflow_id=%s", workflow_id, exc_info=True)

    @staticmethod
    def _outcome(document) -> ExecutionOutcome:
        return ExecutionOutcome(
            workflow_id=document.workflow_id,
            execution_id=document.execution_id,
            publication_id=document.publication_id,
            channel=document.channel.value,
            publisher=document.publisher,
            status=document.status.value,
            attempt=document.attempt,
        )
