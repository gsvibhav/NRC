"""Top-level orchestration for the Publication Preparation Layer
(Milestone 8).

Converts one **approved** draft version into a stable, minimal,
platform-facing publication package — and nothing else. Never generates
or rewrites copy, never reruns Claude (this service makes zero Claude
calls, full stop), never edits the approved draft, never changes the
target channel, never selects a different planned output, never
publishes anything. See src/ai/draft_editing_service.py /
draft_generation_service.py for the AI-pipeline stages this deliberately
does *not* touch.

**Trigger and transaction boundary.** Approval (src/workflow/manager.py's
`approve_draft()`) and publication preparation are two separate domain
writes — never pretended to be one atomic S3 transaction. This service is
called synchronously, immediately after approval already succeeded (see
handlers.py's `_handle_approve_action()`), using the workflow_id already
in hand from that same request — never re-resolving via the Telegram
conversation pointer for the *triggering* call. If the process crashes
between those two writes, or if this service fails retryably, `/retry`
(and, for the "crashed before any attempt was ever made" case, `/status`'s
and `/retry`'s own eligibility check — see handlers.py) resume using the
one already-durable signal available: `workflow.generated_draft.status ==
"APPROVED"` with no `workflow.publication` reference yet. Approval itself
is never reverted, and the approved draft version is never touched,
regardless of how preparation goes.

**Failure taxonomy** — deliberately different from every other pipeline
stage's Claude-retryable/permanent split, because this service makes no
Claude calls at all:

- **Permanent** (`PublicationPermanentError` subclasses — unsupported
  output/media type, missing/inconsistent approval metadata, missing
  content plan or media, failed validation): a specific, enumerated
  business-logic condition. A best-effort `FAILED` publication record is
  persisted for audit (skipped only if not enough is known to construct
  even that, e.g. an entirely unresolvable output type), the active
  pointer is cleared (see below for why this is a deliberate exception to
  most other "permanent failure" pointer-clearing), and the approval
  itself is never revoked.
- **Retryable** (anything else — a generic `PublicationError`, a
  persistence/concurrency conflict, an unexpected workflow-load failure):
  treated as a transient infrastructure hiccup by elimination, since every
  known permanent business-logic condition already has its own specific
  type above. Sets `metadata["pending_retry"] = "publication_preparation"`
  and, critically, **does not clear the active conversation pointer** —
  see the pointer-clearing note below.

**Pointer-clearing is deliberately deferred to this service, not the
approve handler.** Milestone 7's `clear_pointer_if_terminal()` convention
clears the pointer the instant a workflow reaches a terminal state.
Milestone 8 narrows that for the one specific COMPLETED-via-approval path:
the pointer stays active until publication preparation has been finally
resolved — successfully, or permanently failed — precisely so a crash (or
a retryable failure) leaves the workflow reachable via the user's normal
active-pointer resolution for `/retry`/`/status`, without inventing any
new lookup infrastructure. See handlers.py's `_handle_approve_action()`
and README.md's "Completed workflow lookup" for the full reasoning. A
*permanently* failed preparation still clears the pointer (nothing left
to retry), matching every other domain's "permanent means done" meaning —
the one exception is that, unlike other permanent failures, the
underlying *workflow* is not broken here (it's a valid, approved,
completed authoring result), so `/status` can still describe it — see
that function's own docstring in handlers.py.

Idempotency: `PublicationManager.find_existing()` is checked immediately
after eligibility — if an existing `READY_FOR_PUBLISHING` package already
exists for this exact `(workflow_id, output_id)`, it is reused verbatim;
this service never rebuilds or overwrites an authoritative package.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..ai.content_plan_manager import ContentPlanManager
from ..ai.content_plan_models import ContentPlanStatus
from ..ai.draft_manager import DraftManager
from ..ai.draft_version_manager import DraftVersionManager, resolve_current_draft
from ..ai.draft_version_models import ReviewStatus
from ..ai.errors import AnalysisError
from ..conversation.lifecycle import clear_pointer_if_terminal
from ..conversation.manager import ConversationManager
from ..workflow.errors import WorkflowError
from ..workflow.manager import WorkflowManager
from ..workflow.states import WorkflowState
from .builder import build_media_references, build_publication_content
from .errors import (
    ApprovedDraftVersionNotFoundError,
    ApprovedVersionLinkageMismatchError,
    MissingApprovalMetadataForPublicationError,
    MissingContentPlanForPublicationError,
    NoPrimaryOutputForPublicationError,
    PublicationError,
    PublicationPermanentError,
    WorkflowNotEligibleForPublicationError,
)
from .manager import PublicationManager
from .models import (
    PublicationApproval,
    PublicationChannel,
    PublicationDraftReference,
    PublicationStatus,
    resolve_channel,
    resolve_placement,
)
from .validation import validate_publication_package

logger = logging.getLogger(__name__)

_RETRY_PUBLICATION_PREPARATION = "publication_preparation"


@dataclass(frozen=True)
class PublicationOutcome:
    workflow_id: str
    publication_id: str
    output_id: str
    output_type: str
    channel: str
    status: str


class PublicationPreparationService:
    def __init__(
        self,
        *,
        schema_version: int,
        max_instagram_caption_length: int,
        max_hashtags: int,
        s3_bucket_name: str,
        workflow_manager: WorkflowManager,
        draft_manager: DraftManager,
        draft_version_manager: DraftVersionManager,
        content_plan_manager: ContentPlanManager,
        publication_manager: PublicationManager,
        conversation_manager: ConversationManager,
    ) -> None:
        self._schema_version = schema_version
        self._max_instagram_caption_length = max_instagram_caption_length
        self._max_hashtags = max_hashtags
        self._s3_bucket_name = s3_bucket_name
        self._workflow_manager = workflow_manager
        self._draft_manager = draft_manager
        self._draft_version_manager = draft_version_manager
        self._content_plan_manager = content_plan_manager
        self._publication_manager = publication_manager
        self._conversation_manager = conversation_manager

    async def prepare_publication(
        self, *, workflow_id: str, telegram_user_id: int, clear_pointer_on_success: bool = True
    ) -> PublicationOutcome:
        """Prepare (or reuse) the publication package for `workflow_id`'s
        approved draft. Raises a PublicationError subclass on any expected
        failure. Never returns a result unless a package actually reached
        READY_FOR_PUBLISHING (freshly built, or reused). Zero Claude calls
        on every path, including failure paths.

        `clear_pointer_on_success` (Milestone 9): defaults to `True` for a
        standalone call, but the caller may pass `False` when it intends
        to immediately chain a further step (execution creation, see
        src/execution/service.py) and wants pointer-clearing deferred
        until that step, too, is finally resolved — so `/retry`/`/status`
        keep finding the workflow through the ordinary active-pointer path
        across the whole chain, not just this one link of it."""

        logger.info("Publication preparation requested workflow_id=%s telegram_user_id=%s", workflow_id, telegram_user_id)

        try:
            workflow = self._workflow_manager.load_workflow(workflow_id)
        except WorkflowError as exc:
            raise PublicationError(f"failed to load workflow_id={workflow_id}: {exc}") from exc

        generated_draft = workflow.generated_draft or {}
        if workflow.state is not WorkflowState.COMPLETED or generated_draft.get("status") != "APPROVED":
            raise WorkflowNotEligibleForPublicationError(
                f"workflow_id={workflow_id} is not an approved, completed workflow (state={workflow.state.value}, "
                f"generated_draft.status={generated_draft.get('status')!r})"
            )

        output_id = generated_draft.get("output_id")
        approved_output_type = generated_draft.get("output_type")
        approved_draft_id = generated_draft.get("draft_id")
        approved_version_number = generated_draft.get("current_version")
        approved_at = generated_draft.get("approved_at")
        approved_by = generated_draft.get("approved_by_telegram_user_id")
        if (
            not output_id or not approved_output_type or not approved_draft_id or not approved_at
            or approved_version_number is None or approved_by is None
        ):
            raise MissingApprovalMetadataForPublicationError(
                f"workflow_id={workflow_id} generated_draft reference is missing required approval fields"
            )

        existing = self._publication_manager.find_existing(workflow_id, output_id)
        if existing is not None and existing.status is PublicationStatus.READY_FOR_PUBLISHING:
            logger.info(
                "Duplicate publication preparation skipped workflow_id=%s output_id=%s (already READY_FOR_PUBLISHING)",
                workflow_id, output_id,
            )
            self._attach_reference_and_finish(workflow_id, telegram_user_id, existing, clear_pointer_on_success)
            return self._outcome(existing)

        try:
            channel = resolve_channel(approved_output_type)

            current_document, current_pointer, _ = self._resolve_approved_draft(
                workflow_id=workflow_id, output_id=output_id,
                approved_draft_id=approved_draft_id, approved_version_number=approved_version_number,
            )

            plan = self._content_plan_manager.find_existing(workflow_id)
            if plan is None or plan.status is not ContentPlanStatus.COMPLETED:
                raise MissingContentPlanForPublicationError(f"workflow_id={workflow_id} has no completed content plan")
            target_output = next((o for o in plan.outputs if o.output_id == output_id), None)
            if target_output is None:
                raise NoPrimaryOutputForPublicationError(
                    f"workflow_id={workflow_id} plan has no output_id={output_id}"
                )

            media_references = build_media_references(
                workflow.media, channel=channel, bucket_name=self._s3_bucket_name
            )
            content = build_publication_content(current_document.content or {})
            # Milestone 11A: resolved once, here, from the approved
            # media's own MIME type — persisted explicitly so a publisher
            # adapter never has to re-infer it later (see models.py's
            # PublicationPackage.resolved_placement()).
            placement = resolve_placement(channel, media_references[0].media_type)

            validate_publication_package(
                content, media_references,
                max_caption_length=self._max_instagram_caption_length, max_hashtags=self._max_hashtags,
            )
        except PublicationPermanentError as exc:
            logger.error("Publication preparation permanent failure workflow_id=%s error=%s", workflow_id, exc)
            failed_document = self._record_failure(
                workflow_id, output_id, existing,
                plan_id=None, output_type=approved_output_type, channel=None, failure_reason=type(exc).__name__,
            )
            self._mark_permanent_failure(workflow_id, output_id, failed_document)
            raise
        except AnalysisError as exc:
            # resolve_current_draft() (src/ai/draft_version_manager.py)
            # raises this ai-domain hierarchy — translate at the boundary
            # so callers only ever see this domain's own error types.
            translated = ApprovedDraftVersionNotFoundError(str(exc))
            logger.error("Publication preparation permanent failure workflow_id=%s error=%s", workflow_id, translated)
            failed_document = self._record_failure(
                workflow_id, output_id, existing,
                plan_id=None, output_type=approved_output_type, channel=None, failure_reason=type(translated).__name__,
            )
            self._mark_permanent_failure(workflow_id, output_id, failed_document)
            raise translated from exc

        try:
            document = self._persist(
                workflow_id=workflow_id, output_id=output_id, plan_id=plan.plan_id,
                output_type=current_document.output_type, channel=channel, existing=existing,
                content=content, media_references=media_references, placement=placement,
                draft_id=current_document.draft_id, version_number=current_document.version_number,
                draft_schema_version=current_document.schema_version, draft_document_version=current_document.version,
                approved_at=approved_at, approved_by=approved_by,
            )
        except PublicationError as exc:
            logger.warning("Publication preparation retryable failure workflow_id=%s error=%s", workflow_id, exc)
            self._set_pending_retry(workflow_id)
            raise

        self._attach_reference_and_finish(workflow_id, telegram_user_id, document, clear_pointer_on_success)
        logger.info(
            "Publication package persisted workflow_id=%s output_id=%s publication_id=%s",
            workflow_id, output_id, document.publication_id,
        )
        return self._outcome(document)

    def _resolve_approved_draft(self, *, workflow_id: str, output_id: str, approved_draft_id: str, approved_version_number: int):
        current_document, current_pointer, etag = resolve_current_draft(
            draft_version_manager=self._draft_version_manager,
            draft_manager=self._draft_manager,
            workflow_id=workflow_id,
            output_id=output_id,
        )

        if (
            current_pointer.status is not ReviewStatus.APPROVED
            or current_document.draft_id != approved_draft_id
            or current_pointer.current_version_number != approved_version_number
        ):
            raise ApprovedVersionLinkageMismatchError(
                f"workflow_id={workflow_id} output_id={output_id}: current draft/version does not match "
                f"the workflow's approval metadata"
            )

        return current_document, current_pointer, etag

    def _persist(
        self, *, workflow_id, output_id, plan_id, output_type, channel, existing,
        content, media_references, placement, draft_id, version_number, draft_schema_version, draft_document_version,
        approved_at, approved_by,
    ):
        draft_reference = PublicationDraftReference(draft_id=draft_id, version_number=version_number).to_dict()
        approval = PublicationApproval(approved_at=approved_at, approved_by_telegram_user_id=approved_by).to_dict()
        source_versions = {"draft_schema_version": draft_schema_version, "draft_document_version": draft_document_version}
        media_dicts = [m.to_dict() for m in media_references]

        kwargs = dict(
            workflow_id=workflow_id, output_id=output_id, channel=channel, schema_version=self._schema_version,
            status=PublicationStatus.READY_FOR_PUBLISHING, draft=draft_reference, content=content.to_dict(),
            media=media_dicts, approval=approval, source_versions=source_versions, placement=placement.to_dict(),
        )
        if existing is not None:
            return self._publication_manager.supersede_failed_publication(**kwargs)
        return self._publication_manager.create_publication(plan_id=plan_id, output_type=output_type, **kwargs)

    def _record_failure(self, workflow_id, output_id, existing, *, plan_id, output_type, channel, failure_reason: str):
        """Best-effort persistence of a FAILED publication record for
        audit. Returns the persisted document, or None if even this
        failed (never lets a secondary failure mask the original error —
        the caller always still raises the original exception)."""

        if channel is None:
            # Not enough was resolved to name a real channel (e.g. the
            # output type itself is unsupported) — a placeholder value is
            # used purely so a FAILED record can still be persisted for
            # audit; it is never read as if it were real, since nothing
            # ever publishes from a FAILED record.
            channel = PublicationChannel.INSTAGRAM
        kwargs = dict(
            workflow_id=workflow_id, output_id=output_id, channel=channel, schema_version=self._schema_version,
            status=PublicationStatus.FAILED, metadata={"failure_reason": failure_reason},
        )
        try:
            if existing is not None:
                return self._publication_manager.supersede_failed_publication(**kwargs)
            return self._publication_manager.create_publication(
                plan_id=plan_id or "", output_type=output_type or "unknown", **kwargs
            )
        except PublicationError:
            logger.error("Failed to persist publication failure record workflow_id=%s", workflow_id, exc_info=True)
            return None

    def _attach_reference_and_finish(
        self, workflow_id: str, telegram_user_id: int, document, clear_pointer_on_success: bool = True
    ) -> None:
        reference = {
            "publication_id": document.publication_id,
            "output_id": document.output_id,
            "draft_id": (document.draft or {}).get("draft_id"),
            "draft_version": (document.draft or {}).get("version_number"),
            "channel": document.channel.value,
            "status": document.status.value,
            "schema_version": document.schema_version,
            "prepared_at": document.updated_at,
        }
        try:
            self._workflow_manager.attach_publication_reference(workflow_id, reference)
            self._workflow_manager.update_metadata(workflow_id, {"pending_retry": None})
        except WorkflowError:
            logger.error("Failed to attach publication reference workflow_id=%s", workflow_id, exc_info=True)

        if not clear_pointer_on_success:
            return

        clear_pointer_if_terminal(
            telegram_user_id=telegram_user_id, workflow_state=WorkflowState.COMPLETED,
            conversation_manager=self._conversation_manager,
        )

    def _set_pending_retry(self, workflow_id: str) -> None:
        try:
            self._workflow_manager.update_metadata(workflow_id, {"pending_retry": _RETRY_PUBLICATION_PREPARATION})
        except WorkflowError:
            logger.error("Failed to set pending_retry workflow_id=%s", workflow_id, exc_info=True)

    def _mark_permanent_failure(self, workflow_id: str, output_id: str, failed_document) -> None:
        """Deliberately does NOT clear the active conversation pointer,
        unlike every other permanent-failure path in this codebase. The
        underlying authoring workflow stays `COMPLETED` and perfectly
        valid — only the publication *package* is unresolved — so
        `/status` needs to keep resolving this workflow as the caller's
        active one in order to report "permanently unavailable" rather
        than "no active post" (see handlers.py's `_describe_workflow_state()`).
        The user's manual escape hatch is unchanged: `/cancel` already
        clears the pointer for any terminal-state workflow without
        re-rejecting the approved draft, so nothing is stuck forever.

        Also attaches a lightweight `status: "FAILED"` workflow reference
        (when a failure record was actually persisted) so handlers.py can
        distinguish "permanently unavailable" from "never attempted" /
        "retry pending" without an extra publication-store lookup on every
        /status call — see this module's docstring."""

        try:
            self._workflow_manager.update_metadata(workflow_id, {"pending_retry": None})
        except WorkflowError:
            logger.error("Failed to clear pending_retry workflow_id=%s", workflow_id, exc_info=True)

        if failed_document is None:
            return
        reference = {
            "publication_id": failed_document.publication_id,
            "output_id": output_id,
            "status": failed_document.status.value,
            "schema_version": failed_document.schema_version,
            "prepared_at": failed_document.updated_at,
        }
        try:
            self._workflow_manager.attach_publication_reference(workflow_id, reference)
        except WorkflowError:
            logger.error("Failed to attach FAILED publication reference workflow_id=%s", workflow_id, exc_info=True)

    @staticmethod
    def _outcome(document) -> PublicationOutcome:
        return PublicationOutcome(
            workflow_id=document.workflow_id,
            publication_id=document.publication_id,
            output_id=document.output_id,
            output_type=document.output_type,
            channel=document.channel.value,
            status=document.status.value,
        )
