"""Telegram command and media handlers for NRC Social Agent.

/start and /help are still Milestone 1 placeholders — plain, static text.

`media()` (Milestone 2) implements `RECEIVING_MEDIA` → `UPLOADING_MEDIA` →
(success, or `FAILED`) from docs/WORKFLOW.md, (Milestone 3/3.5) creates and
persists a workflow record plus points the user's conversation at it,
(Milestone 4A) invokes src/ai/analysis_service.py for a single-pass Claude
analysis of the uploaded photo, and (Milestone 4B) immediately runs the
first adaptive-clarification decision cycle (src/ai/clarification_service.py)
— replying with either the generated question or a short "ready to
generate content" message, never the full structured analysis/context,
model name, or token usage. It also now refuses a new upload outright
(before touching S3 or Claude) if the caller already has a non-terminal
workflow in progress — see docs/WORKFLOW.md §5.

`text_reply()` (Milestone 4B) is the upgraded plain-text fallback: for an
authorized user with an active workflow in `WAITING_FOR_USER`, it routes
the reply through `ClarificationService.handle_user_reply()`; otherwise it
gives a safe, generic response without ever calling Claude.

`retry()` (Milestone 4B, extended in 5) re-attempts the one pending
retryable AI operation (`metadata["pending_retry"]`, set by
analysis_service.py / clarification_service.py / content_planning_service.py)
for the caller's active workflow — see README.md's "Retryable versus
permanent failures".

Once clarification reaches `GENERATING_CONTENT` (Milestone 5), both
`_run_clarification_and_reply()` (called from `media()`, `text_reply()`,
and `retry()`'s "analysis"/"clarification_decision" branches) and
`retry()`'s "content_planning" branch chain straight into
`src/ai/content_planning_service.py` — the user never needs a separate
command for it. Only a short, derived summary of the persisted plan is
ever sent — never the full strategy, priorities, or excluded-output
reasoning.

Once a content plan completes (Milestone 6), `_run_planning_and_reply()`
chains straight into `src/ai/draft_generation_service.py` — again with no
separate user command needed — and `retry()` gained a fourth pending-
operation value, `"primary_draft_generation"`. The resulting draft is
shown via `_format_draft_preview_message()` as a plain-text "ready for
your review" preview — never the persisted draft object, internal field
names, model/token details, or the word "published".

`status()` and `cancel()` (Milestone 3.5, extended in 4B for
`GENERATING_CONTENT`) resolve the caller's active workflow via
src/conversation/resolution.py — the single source of truth for "which
workflow is this user talking about" — rather than guessing or requiring
the user to supply a workflow_id.

Milestone 7 adds the Telegram Draft Review, Editing and Approval Workflow:
`review_action()` is the `CallbackQueryHandler` entry point for the four
inline buttons (`Approve` / `Edit` / `Save Draft` / `Reject`) every draft
preview now carries (see `_build_review_keyboard()`), each tied to a
specific `(workflow_id, version_number)` pair and always re-resolved
against the *authoritative* current version before acting — a stale
button (from a superseded preview) is detected and rejected with zero
side effects, never silently honored. `text_reply()` gained a branch for
`EDITING`: the next authorized text message is treated as the active
edit instruction and handed to `src/ai/draft_editing_service.py`, never
re-running clarification, analysis, or planning. `/retry` gained a fifth
pending-operation value, `"draft_editing"`. Every review action's success
message explicitly frames the result as a review decision, never as
publishing.

Milestone 8 chains the Publication Preparation Layer (`src/publication/`)
directly after a successful approve, via `_run_publication_preparation_and_reply()`
(shared with `/retry`'s new `"publication_preparation"` branch) — approval
itself (`WorkflowManager.approve_draft()`) is always durable and is never
reverted regardless of how preparation goes. The active pointer is
deliberately NOT cleared immediately on approve (a narrow, documented
deviation from `clear_pointer_if_terminal()`'s usual "terminal state ⇒
clear now" convention — see `src/publication/service.py`'s module
docstring): it stays active until preparation is finally resolved, so a
crash mid-preparation, a retryable failure, or a permanent failure all
leave the workflow reachable through the caller's ordinary active-pointer
resolution for `/retry` and `/status`, with no new lookup mechanism
needed. Every approve/retry reply about preparation is phrased to never
say "published" (`APPROVE_AND_PREPARED_MESSAGE` says "prepared for
publishing," `APPROVE_PREPARATION_RETRY_MESSAGE` explicitly says "the
draft is safe," never "approval failed")."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .access_control import is_live_test_operator, restricted
from .ai.clarification_models import ClarificationDecisionType
from .ai.content_plan_models import OUTPUT_TYPE_REGISTRY, OutputType
from .ai.draft_version_manager import resolve_current_draft
from .ai.draft_version_models import ReviewStatus
from .ai.errors import AnalysisError
from .conversation.errors import ConversationError, NoActiveWorkflowError
from .conversation.lifecycle import clear_pointer_if_terminal
from .conversation.resolution import resolve_active_workflow
from .execution.errors import ExecutionError, LiveValidationError, NotLiveTestOperatorError, UnauthorizedDispatchError
from .execution.models import DispatchCheckpoint, ExecutionStatus
from .media.errors import MediaIngestionError
from .media.ingestion import ingest_media
from .publication.errors import PublicationError, PublicationPermanentError
from .publisher.models import FailureCategory
from .workflow.errors import WorkflowError
from .workflow.states import TERMINAL_STATES, WorkflowState

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


START_MESSAGE = (
    "NRC Social Agent\n\n"
    "Send a photo or video here to start creating a post. I'll analyze it, "
    "ask any follow-up questions I need, and show you a preview before "
    "anything is finalized.\n\n"
    "(This build uploads your media and runs an initial photo analysis. "
    "Follow-up questions, captions, and previews are coming soon — video "
    "analysis isn't supported yet.)"
)

HELP_MESSAGE = (
    "How NRC Social Agent works:\n\n"
    "1. Send a photo or video.\n"
    "2. I'll ask any follow-up questions I need.\n"
    "3. I'll generate a caption, hashtags, SEO keywords, category, alt text, "
    "and metadata.\n"
    "4. You'll get a preview with Approve / Edit / Save Draft / Reject.\n\n"
    "Commands:\n"
    "/start — how to begin\n"
    "/help — this message\n"
    "/status — check your current post's status\n"
    "/cancel — cancel the post you're working on\n"
    "/retry — retry the last step if it couldn't complete\n"
    "/instagram_status — check Instagram publishing diagnostics\n"
    "/instagram_test_publish — operators only: controlled Instagram live validation\n\n"
    "(This build implements photo upload, an initial analysis step, and an "
    "adaptive follow-up conversation. Content generation and previews "
    "aren't live yet.)"
)

STATUS_MESSAGE_NO_ACTIVE_POST = "No active post right now — send a photo or video to begin."
STATUS_MESSAGE_TEMPLATE = "{description}.\nReference: {workflow_id}"

# Plain-language descriptions for /status — no AI/draft/analysis content,
# just what the persisted workflow state means to the user.
STATE_DESCRIPTIONS = {
    WorkflowState.IDLE: "No active post",
    WorkflowState.RECEIVING_MEDIA: "Receiving your media",
    WorkflowState.UPLOADING_MEDIA: "Uploading your media",
    WorkflowState.ANALYZING_MEDIA: "Analyzing your media",
    WorkflowState.WAITING_FOR_USER: "Waiting on your reply",
    WorkflowState.GENERATING_CONTENT: "Ready to generate your content",
    WorkflowState.SHOWING_PREVIEW: "Your draft is ready for review",
    WorkflowState.EDITING: "Waiting for your edit instructions",
    WorkflowState.COMPLETED: "Your draft has been approved and prepared for publishing. Nothing has been published yet",
    WorkflowState.FAILED: "That post failed",
    WorkflowState.SAVED_AS_DRAFT: "Saved as a draft. Nothing has been published",
    WorkflowState.REJECTED: "Rejected",
}

CANCEL_MESSAGE_NOTHING_TO_CANCEL = "Nothing to cancel right now."
CANCEL_MESSAGE_CANCELLED_TEMPLATE = "Post cancelled.\nReference: {workflow_id}"

UNHANDLED_MESSAGE = (
    "I can only respond to text replies and commands right now (/start, "
    "/help, /status, /cancel, /retry)."
)

MEDIA_SUCCESS_TEMPLATE = (
    "Got it — your {media_type} was uploaded successfully.\nReference: {workflow_id}\n\n"
    "Analyzing your content…"
)

# Deliberately no separate "analysis complete" message with the raw
# summary: per the brief's conversation-quality requirements, the bot
# should feel like a continuous conversation, not a sequence of status
# reports — the very next message is either the first adaptive question
# or CLARIFICATION_SUFFICIENT_MESSAGE below.

ACTIVE_WORKFLOW_IN_PROGRESS_TEMPLATE = (
    "You already have a post in progress.\nReference: {workflow_id}\n\n"
    "Please /cancel it first if you'd like to start a new one."
)

CLARIFICATION_SUFFICIENT_MESSAGE = "Perfect — I have enough context to prepare the content."

TEXT_REPLY_NO_ACTIVE_WORKFLOW_MESSAGE = "No active post right now — send a photo or video to begin."
TEXT_REPLY_NOT_WAITING_MESSAGE = "I'm not waiting on an answer right now — send a photo or video to start a post."

RETRY_MESSAGE_NOTHING_TO_RETRY = "Nothing to retry right now."

PLANNING_STARTED_MESSAGE = "Planning the best content direction for this asset…"
PLAN_READY_SINGLE_TEMPLATE = "Content plan ready.\n\nRecommended: {output}\n\nNext, I'll prepare the first draft."
PLAN_READY_MULTI_TEMPLATE = (
    "Content plan ready.\n\nRecommended:\n{output_list}\n\nNext, I'll prepare the first draft."
)

DRAFT_PREVIEW_HEADER = "Here's a draft ready for your review:"
# Explicit "ready for review" framing per the brief — never implies
# anything has been sent or published anywhere.
DRAFT_PREVIEW_FOOTER = "\n\nThis is ready for your review — nothing has been published."

# Telegram's own message-length ceiling (not the Instagram caption limit —
# see config.py's MAX_INSTAGRAM_CAPTION_LENGTH, already enforced on the
# draft's content itself before this preview is ever built). This is a
# last-resort defensive truncation of the *rendered preview text*, which
# should be unreachable given how much smaller the caption ceiling is.
TELEGRAM_MESSAGE_LENGTH_LIMIT = 4096

DRAFT_GENERATION_STARTED_MESSAGE = "Preparing the first draft for review…"

# --- Milestone 7: Telegram Draft Review, Editing and Approval Workflow ---

EDIT_MODE_PROMPT_MESSAGE = (
    "Tell me what you would like changed.\n\n"
    "For example: \"Make it shorter and remove the sales tone.\""
)
EDIT_PROCESSING_MESSAGE = "Applying your edit…"

SAVE_DRAFT_SUCCESS_MESSAGE = "Saved as a draft.\n\nNothing has been published."
REJECT_SUCCESS_MESSAGE = "Draft rejected.\n\nNothing has been published."

STALE_ACTION_MESSAGE = "A newer version of this draft is already available — here's the latest:"
REVIEW_ACTION_UNAVAILABLE_MESSAGE = "That action isn't available right now."
REVIEW_ACTION_NO_ACTIVE_WORKFLOW_MESSAGE = "No active post right now — send a photo or video to begin."

_REVIEW_ACTIONS = {"approve", "edit", "save", "reject"}

# --- Milestone 8: Publication Preparation Layer ---

APPROVE_AND_PREPARED_MESSAGE = (
    "Approved.\n\nYour content has been prepared for publishing. Nothing has been published yet."
)
APPROVE_PREPARATION_RETRY_MESSAGE = (
    "Approved.\n\nThe draft is safe, but I could not prepare it for publishing right now. "
    "You can retry this step."
)
APPROVE_PREPARATION_UNAVAILABLE_MESSAGE = (
    "The draft is approved, but this output is not yet supported for publishing preparation."
)

STATUS_PUBLICATION_RETRY_PENDING_DESCRIPTION = (
    "Your draft has been approved. Publishing preparation is pending — you can retry with /retry"
)
STATUS_PUBLICATION_PERMANENTLY_UNAVAILABLE_DESCRIPTION = (
    "Your draft has been approved, but this output is not yet supported for publishing preparation"
)

# --- Milestone 10: Instagram Publisher Adapter and Dispatch Lifecycle ---

DISPATCH_STARTED_MESSAGE = "Publishing to Instagram…"
DISPATCH_COMPLETED_MESSAGE = "Published to Instagram successfully."
DISPATCH_IN_PROGRESS_MESSAGE = "Instagram publication is already in progress."
DISPATCH_RETRYABLE_FAILURE_MESSAGE = (
    "Instagram could not complete the publication right now. Your approved content is safe "
    "and this dispatch can be retried."
)
DISPATCH_PERMANENT_FAILURE_MESSAGE = (
    "Instagram could not publish this content. Your approved draft and publication package are still safe."
)
INSTAGRAM_PUBLISHING_DISABLED_MESSAGE = "Publishing to Instagram isn't enabled right now."

STATUS_DISPATCH_IN_PROGRESS_DESCRIPTION = "Instagram publication is currently in progress"
STATUS_DISPATCH_PROCESSING_MEDIA_DESCRIPTION = "Instagram is still processing the media"
STATUS_DISPATCH_RETRYABLE_FAILURE_DESCRIPTION = "Instagram publication did not complete. It can be retried with /retry"
STATUS_DISPATCH_PERMANENT_FAILURE_DESCRIPTION = (
    "Instagram could not publish this content. The approved content is still saved"
)
STATUS_DISPATCH_COMPLETED_DESCRIPTION = "Published to Instagram successfully"

# --- Milestone 11A: Instagram Operational Diagnostics ---
# Note: implemented as /instagram_status, not the literal /instagram-status
# named in the brief — python-telegram-bot's CommandHandler (and Telegram's
# own bot-command rules) reject hyphens in command names (only letters,
# digits, and underscores are valid), so /instagram_status is the closest
# valid equivalent.

INSTAGRAM_STATUS_HEADER = "Instagram diagnostics"

# --- Milestone 11B: Controlled Instagram Live Validation ---
# Note: implemented as /instagram_test_publish, not /instagram-test-publish
# — same Telegram command-name restriction as /instagram_status above.

LIVE_VALIDATION_NOT_OPERATOR_MESSAGE = "That action isn't available to you."
LIVE_VALIDATION_NO_CANDIDATES_TEMPLATE = "No eligible publication was found for controlled live validation.\n\n{reason}"
LIVE_VALIDATION_MULTIPLE_CANDIDATES_MESSAGE = "Multiple eligible publications were found — choose one:"
LIVE_VALIDATION_CANCELLED_MESSAGE = "Controlled live validation cancelled. Nothing was published."
LIVE_VALIDATION_UNAVAILABLE_MESSAGE = (
    "This live-validation confirmation has already been used or has expired. Please request a new preview "
    "with /instagram_test_publish."
)
LIVE_VALIDATION_AMBIGUOUS_MESSAGE = (
    "Meta may have accepted the publication, but the final result could not be confirmed.\n\n"
    "Do not retry or create another publication.\n\n"
    "Check the Instagram account manually and follow the operator recovery procedure "
    "(see docs/INSTAGRAM_LIVE_VALIDATION.md)."
)
LIVE_VALIDATION_RETRYABLE_FAILURE_TEMPLATE = (
    "{base}\n\nYou can resume with /retry — the existing confirmation already authorized this exact "
    "execution, so a new confirmation is not required."
)
LIVE_VALIDATION_HEADER = "Controlled Instagram Live Validation"


def _format_plan_ready_message(outputs: list) -> str:
    """Renders only display names, in priority order — never internal
    priorities, schema fields, confidence, model name, token usage, or
    excluded-output reasoning."""

    display_names = [OUTPUT_TYPE_REGISTRY[OutputType(o.output_type)].display_name for o in outputs]
    if len(display_names) == 1:
        return PLAN_READY_SINGLE_TEMPLATE.format(output=display_names[0])
    bullet_list = "\n".join(f"• {name}" for name in display_names)
    return PLAN_READY_MULTI_TEMPLATE.format(output_list=bullet_list)


def _format_instagram_reel_caption_preview(content: dict) -> str:
    """Plain-text rendering only — this codebase never sets a Telegram
    parse_mode, so there is no Markdown/HTML to escape; the persisted
    draft's own text is shown verbatim, just reassembled with the
    hashtags/CTA on their own lines."""

    caption = content.get("caption") or ""
    cta = content.get("cta")
    hashtags = content.get("hashtags") or []

    body_parts = [caption]
    if cta:
        body_parts.append(cta)
    if hashtags:
        body_parts.append(" ".join(f"#{tag.lstrip('#')}" for tag in hashtags))
    return "\n\n".join(body_parts)


_PREVIEW_FORMATTERS_BY_OUTPUT_TYPE = {
    "instagram_reel_caption": _format_instagram_reel_caption_preview,
}


def _format_draft_preview_message(output_type: str, content: dict, version_number: int) -> str:
    """Builds the Telegram preview for a draft — never the persisted draft
    object itself, never internal field names, never a model/token/
    confidence detail, and never the word "published" (this is a review
    step, not a publish step). A dedicated formatter per output type keeps
    this from forcing every future format into one generic shape; falls
    back to the raw caption text if a formatter is ever missing (should be
    unreachable — every supports_generation=True output type has one).

    Always names the current version explicitly (required so the reviewer
    can tell a revised preview apart from the original, and so the
    attached buttons' embedded version number is visibly consistent with
    what's shown) — version 1 reads as the original "ready for review"
    framing, version 2+ reads as "Updated draft"."""

    formatter = _PREVIEW_FORMATTERS_BY_OUTPUT_TYPE.get(output_type)
    body = formatter(content) if formatter is not None else str(content.get("caption", ""))
    if version_number == 1:
        header = f"{DRAFT_PREVIEW_HEADER}\n\nVersion {version_number}"
    else:
        header = f"Updated draft — Version {version_number}:"
    message = f"{header}\n\n{body}{DRAFT_PREVIEW_FOOTER}"
    if len(message) > TELEGRAM_MESSAGE_LENGTH_LIMIT:
        message = message[: TELEGRAM_MESSAGE_LENGTH_LIMIT - 1] + "…"
    return message


def _build_review_keyboard(workflow_id: str, version_number: int) -> InlineKeyboardMarkup:
    """Callback data is `review:<action>:<workflow_id>:<version_number>` —
    compact (well under Telegram's 64-byte callback_data ceiling even with
    a full 32-char hex workflow_id), tied to both the workflow and the
    exact version being reviewed (needed for stale-action detection), and
    never trusted as the source of truth — see review_action()'s
    server-side authoritative-version check. `workflow_id` is used as-is,
    not shortened: this application already exposes it to the user
    verbatim as "Reference: <workflow_id>" in every other message (upload
    ack, /status, /cancel), so this isn't a new exposure."""

    def cb(action: str) -> str:
        return f"review:{action}:{workflow_id}:{version_number}"

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Approve", callback_data=cb("approve")), InlineKeyboardButton("Edit", callback_data=cb("edit"))],
            [InlineKeyboardButton("Save Draft", callback_data=cb("save")), InlineKeyboardButton("Reject", callback_data=cb("reject"))],
        ]
    )


def _parse_review_callback_data(data: str | None):
    """Strictly parses `review:<action>:<workflow_id>:<version_number>`.
    Returns (action, workflow_id, version_number) or None for anything
    malformed — never partially interpreted or trusted."""

    if not data:
        return None
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != "review":
        return None
    _, action, workflow_id, version_raw = parts
    if action not in _REVIEW_ACTIONS or not workflow_id:
        return None
    try:
        version_number = int(version_raw)
    except ValueError:
        return None
    if version_number < 1:
        return None
    return action, workflow_id, version_number


def _build_dispatch_keyboard(publication_id: str) -> InlineKeyboardMarkup:
    """Callback data is `dispatch:<publisher_name>:<publication_id>` —
    never a token, an account ID, or any publication content. `publication_id`
    is the one stable, deterministic identifier the dispatch service needs
    to resolve the authoritative execution (see src/execution/dispatch_service.py) —
    never trusted as authoritative on its own; every dispatch action still
    re-verifies ownership via the workflow the execution belongs to."""

    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Publish to Instagram", callback_data=f"dispatch:instagram:{publication_id}")]]
    )


def _parse_dispatch_callback_data(data: str | None):
    """Strictly parses `dispatch:<publisher_name>:<publication_id>`.
    Returns (publisher_name, publication_id) or None for anything
    malformed."""

    if not data:
        return None
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "dispatch":
        return None
    _, publisher_name, publication_id = parts
    if publisher_name != "instagram" or not publication_id:
        return None
    return publisher_name, publication_id


def _describe_dispatch_outcome(outcome) -> str:
    if outcome.status in ("completed", "already_completed"):
        permalink = (outcome.result or {}).get("permalink")
        if permalink:
            return f"{DISPATCH_COMPLETED_MESSAGE}\n{permalink}"
        return DISPATCH_COMPLETED_MESSAGE
    if outcome.status == "in_progress":
        return DISPATCH_IN_PROGRESS_MESSAGE
    if outcome.status == "retryable_failure":
        return DISPATCH_RETRYABLE_FAILURE_MESSAGE
    return DISPATCH_PERMANENT_FAILURE_MESSAGE


def _format_instagram_status_message(result) -> str:
    """Renders an `InstagramDiagnosticsResult`
    (src/publisher/instagram/diagnostics.py) as the safe, reviewer-facing
    /instagram_status reply — exactly the five lines this milestone's
    brief calls for (Status/Token/Permissions/Account/Publishing), never
    the access token, a raw Graph API response, or the full Instagram
    account id (only `@username`, when verified)."""

    if result.instagram_ready:
        status_line = "Ready to publish"
    elif result.failure_reason:
        status_line = f"Not ready — {result.failure_reason}"
    else:
        status_line = "Not ready"

    if result.token_valid is None:
        token_line = "Not checked"
    else:
        token_line = "Valid" if result.token_valid else "Invalid"

    if result.permissions_ok is None:
        permissions_line = "Not checked"
    else:
        permissions_line = "Verified" if result.permissions_ok else "Missing required permissions"

    if result.account_verified is None:
        account_line = "Not checked"
    elif result.account_verified:
        account_line = f"Verified (@{result.account_username})" if result.account_username else "Verified"
    else:
        account_line = "Could not verify"

    publishing_line = "Enabled" if result.publishing_enabled else "Disabled"

    return (
        f"{INSTAGRAM_STATUS_HEADER}\n\n"
        f"Status: {status_line}\n"
        f"Token: {token_line}\n"
        f"Permissions: {permissions_line}\n"
        f"Account: {account_line}\n"
        f"Publishing: {publishing_line}"
    )


def _format_live_validation_preview_message(preview) -> str:
    """Renders a `LiveValidationPreview`
    (src/execution/live_validation.py) as the safe confirmation screen
    the milestone's brief specifies — never the access token, a raw
    account ID, a presigned media URL, or a raw Graph response; only the
    already-masked `account_fingerprint` and the verified `@username`."""

    version = preview.approved_version_number if preview.approved_version_number is not None else "unknown"
    media_word = "image" if preview.media_count == 1 else "images"
    return (
        f"{LIVE_VALIDATION_HEADER}\n\n"
        f"Account:\n@{preview.account_username}\n"
        f"Account ID:\n{preview.account_fingerprint}\n\n"
        f"Publication:\n{preview.publication_id}\n\n"
        "Placement:\nInstagram Feed\n\n"
        f"Media:\nSingle JPEG {media_word} ({preview.media_count})\n\n"
        f"Approved version:\n{version}\n\n"
        "Execution status:\nReady for dispatch\n\n"
        "This will create one real public Instagram post using the approved caption, unchanged. "
        "This action cannot be automatically undone.\n\n"
        f"This confirmation expires at {preview.expires_at}.\n\n"
        "If Meta's outcome is ambiguous, manual review will be required — this will never be retried automatically."
    )


def _build_live_validation_confirmation_keyboard(publication_id: str, confirmation_token: str) -> InlineKeyboardMarkup:
    """Callback data is `ilv:c:<publication_id>:<token>` (confirm) /
    `ilv:x:<publication_id>:<token>` (cancel) — never a token, an
    account ID, or any publication content. Deliberately a different
    callback namespace from `dispatch:...` (the ordinary "Publish to
    Instagram" button) — the two must never be confused or accidentally
    routed to each other's handler."""

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Confirm live publication", callback_data=f"ilv:c:{publication_id}:{confirmation_token}")],
        [InlineKeyboardButton("Cancel", callback_data=f"ilv:x:{publication_id}:{confirmation_token}")],
    ])


def _build_live_validation_selection_keyboard(candidates: list) -> InlineKeyboardMarkup:
    """Only reachable if `find_eligible_candidates()` ever returns more
    than one candidate — structurally unreachable under the current
    one-active-workflow-per-operator architecture (see
    live_validation.py's own docstring), implemented here for forward
    compatibility. Always keyed by the exact `publication_id`, never a
    list position."""

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Publication …{c.publication_id[-8:]}", callback_data=f"ilv:s:{c.publication_id}")]
        for c in candidates
    ])


def _parse_live_validation_callback_data(data: str | None):
    """Strictly parses `ilv:s:<publication_id>` (select),
    `ilv:c:<publication_id>:<token>` (confirm), or
    `ilv:x:<publication_id>:<token>` (cancel). Returns
    `(action, publication_id, token_or_none)` or None for anything
    malformed."""

    if not data:
        return None
    parts = data.split(":")
    if len(parts) < 3 or parts[0] != "ilv":
        return None
    action = parts[1]
    if action == "s" and len(parts) == 3:
        return "s", parts[2], None
    if action in ("c", "x") and len(parts) == 4:
        return action, parts[2], parts[3]
    return None


def _describe_live_validation_outcome(outcome) -> str:
    """Translates a controlled-live-validation `DispatchOutcome`
    (src/execution/dispatch_service.py, unchanged — the exact same type
    `dispatch_action()` already describes via `_describe_dispatch_outcome()`)
    into the Part L reporting rules this milestone specifies: a
    completed publish is reported with a shortened media id and a
    reminder to verify manually; an ambiguous outcome is reported with an
    explicit "do not retry" warning and never a normal retry prompt; an
    ordinary retryable failure explicitly notes the existing confirmation
    already covers a `/retry` (no new confirmation needed, per this
    milestone's own recommended `/retry` rule)."""

    if outcome.status in ("completed", "already_completed"):
        result = outcome.result or {}
        lines = ["Published to Instagram successfully.", "Exactly one live publication was attempted."]
        media_id = result.get("external_media_id")
        if media_id:
            lines.append(f"Media ID: …{media_id[-6:]}")
        permalink = result.get("permalink")
        if permalink:
            lines.append(permalink)
        lines.append("Please inspect the post manually on Instagram to confirm it looks correct.")
        return "\n".join(lines)

    if outcome.status == "in_progress":
        return DISPATCH_IN_PROGRESS_MESSAGE

    failure = outcome.failure or {}
    if failure.get("category") == FailureCategory.AMBIGUOUS_PUBLISH_OUTCOME.value:
        return LIVE_VALIDATION_AMBIGUOUS_MESSAGE

    if outcome.status == "retryable_failure":
        return LIVE_VALIDATION_RETRYABLE_FAILURE_TEMPLATE.format(base=DISPATCH_RETRYABLE_FAILURE_MESSAGE)

    return DISPATCH_PERMANENT_FAILURE_MESSAGE


async def _send_draft_preview(message, *, workflow_id: str, output_type: str, content: dict, version_number: int) -> None:
    """The one place a review-ready preview is ever sent — always paired
    with the review-action buttons for the exact version shown. A preview
    delivery failure is logged, never treated as a generation/edit
    failure and never rolled back (see module docstring's preview-
    delivery-failure note)."""

    try:
        await message.reply_text(
            _format_draft_preview_message(output_type, content, version_number),
            reply_markup=_build_review_keyboard(workflow_id, version_number),
        )
    except Exception:
        logger.exception(
            "Failed to send draft preview workflow_id=%s version=%s (draft already persisted, "
            "workflow already in the review state — not treated as a failure)",
            workflow_id, version_number,
        )


@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(START_MESSAGE)


@restricted
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_MESSAGE)


@restricted
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    telegram_user_id = update.effective_user.id
    conversation_manager = context.bot_data["conversation_manager"]
    workflow_manager = context.bot_data["workflow_manager"]

    try:
        document = resolve_active_workflow(telegram_user_id, conversation_manager, workflow_manager)
    except NoActiveWorkflowError:
        await message.reply_text(STATUS_MESSAGE_NO_ACTIVE_POST)
        return
    except ConversationError as exc:
        logger.warning("Status resolution failed user_id=%s error=%s", telegram_user_id, exc)
        await message.reply_text(exc.user_message)
        return

    execution_manager = context.bot_data["execution_manager"]
    description = _describe_workflow_state(document, execution_manager=execution_manager)
    await message.reply_text(
        STATUS_MESSAGE_TEMPLATE.format(description=description, workflow_id=document.workflow_id)
    )


def _describe_workflow_state(document, *, execution_manager) -> str:
    """Plain-language /status description, refined for the two Milestone 7
    situations STATE_DESCRIPTIONS' static mapping can't capture on its
    own: naming the current version while showing a preview, and
    describing a pending edit retry distinctly from ordinary
    "ready to generate" (both share the GENERATING_CONTENT state — see
    src/workflow/states.py). Never exposes raw internal state names.

    Milestone 10: once a package is READY_FOR_PUBLISHING, also looks up
    the (at most one) execution record for it to describe dispatch
    progress — ready/in-progress/processing-media/retryable-failed/
    permanently-failed/completed — never says "published" until the
    execution is durably COMPLETED, and never exposes the raw internal
    status/checkpoint enum values themselves."""

    if document.state is WorkflowState.SHOWING_PREVIEW:
        current_version = (document.generated_draft or {}).get("current_version")
        if current_version is not None:
            return f"Your draft is ready for review.\nCurrent version: {current_version}"
        return STATE_DESCRIPTIONS[WorkflowState.SHOWING_PREVIEW]

    if document.state is WorkflowState.EDITING:
        expected_version = (document.pending_edit or {}).get("expected_parent_version")
        if expected_version is not None:
            return f"I'm waiting for your edit instructions for version {expected_version}"
        return STATE_DESCRIPTIONS[WorkflowState.EDITING]

    if document.state is WorkflowState.GENERATING_CONTENT and document.metadata.get("pending_retry") == "draft_editing":
        return "Your draft is still available. The requested edit can be retried with /retry"

    if document.state is WorkflowState.COMPLETED:
        # Milestone 8: COMPLETED alone doesn't say whether publication
        # preparation succeeded, is pending retry, or hit an unsupported-
        # output condition — distinguished using only the workflow's own
        # lightweight `publication` reference and `pending_retry` marker,
        # exactly like _describe_publication_outcome() above. Never
        # reports "PUBLISHED" and never exposes internal state names.
        publication = document.publication or {}
        if publication.get("status") == "FAILED":
            return STATUS_PUBLICATION_PERMANENTLY_UNAVAILABLE_DESCRIPTION
        if document.metadata.get("pending_retry") in ("publication_preparation", "publication_execution"):
            return STATUS_PUBLICATION_RETRY_PENDING_DESCRIPTION

        publication_id = publication.get("publication_id")
        if publication.get("status") == "READY_FOR_PUBLISHING" and publication_id:
            execution = execution_manager.find_existing(publication_id)
            if execution is not None:
                return _describe_execution_state(execution)

        return STATE_DESCRIPTIONS[WorkflowState.COMPLETED]

    return STATE_DESCRIPTIONS.get(document.state, document.state.value)


def _describe_execution_state(execution) -> str:
    """Milestone 10: translates an ExecutionDocument's internal
    status/checkpoint into the six /status outcomes this milestone
    documents — never exposes the raw enum values, never says "published"
    for anything short of a durably COMPLETED execution."""

    if execution.status is ExecutionStatus.COMPLETED:
        return STATUS_DISPATCH_COMPLETED_DESCRIPTION
    if execution.status is ExecutionStatus.DISPATCH_IN_PROGRESS:
        if execution.checkpoint in (DispatchCheckpoint.CONTAINER_CREATED, DispatchCheckpoint.CONTAINER_PROCESSING):
            return STATUS_DISPATCH_PROCESSING_MEDIA_DESCRIPTION
        return STATUS_DISPATCH_IN_PROGRESS_DESCRIPTION
    if execution.status is ExecutionStatus.DISPATCH_FAILED:
        if (execution.failure or {}).get("retryable"):
            return STATUS_DISPATCH_RETRYABLE_FAILURE_DESCRIPTION
        return STATUS_DISPATCH_PERMANENT_FAILURE_DESCRIPTION
    # READY_FOR_DISPATCH: prepared, awaiting the explicit Publish action.
    return STATE_DESCRIPTIONS[WorkflowState.COMPLETED]


@restricted
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    telegram_user_id = update.effective_user.id
    conversation_manager = context.bot_data["conversation_manager"]
    workflow_manager = context.bot_data["workflow_manager"]

    try:
        document = resolve_active_workflow(telegram_user_id, conversation_manager, workflow_manager)
    except NoActiveWorkflowError:
        await message.reply_text(CANCEL_MESSAGE_NOTHING_TO_CANCEL)
        return
    except ConversationError as exc:
        logger.warning("Cancel resolution failed user_id=%s error=%s", telegram_user_id, exc)
        await message.reply_text(exc.user_message)
        return

    if document.state in TERMINAL_STATES:
        logger.info(
            "Cancel requested for already-terminal workflow user_id=%s workflow_id=%s state=%s",
            telegram_user_id,
            document.workflow_id,
            document.state.value,
        )
        # A terminal workflow should never still be "active" — clear a
        # stale pointer if one was left behind.
        clear_pointer_if_terminal(
            telegram_user_id=telegram_user_id,
            workflow_state=document.state,
            conversation_manager=conversation_manager,
        )
        await message.reply_text(CANCEL_MESSAGE_NOTHING_TO_CANCEL)
        return

    try:
        workflow_manager.update_state(document.workflow_id, WorkflowState.REJECTED)
    except WorkflowError as exc:
        logger.warning(
            "Cancel failed user_id=%s workflow_id=%s error=%s",
            telegram_user_id,
            document.workflow_id,
            exc,
        )
        await message.reply_text(exc.user_message)
        return

    clear_pointer_if_terminal(
        telegram_user_id=telegram_user_id,
        workflow_state=WorkflowState.REJECTED,
        conversation_manager=conversation_manager,
    )
    logger.info("Workflow cancelled user_id=%s workflow_id=%s", telegram_user_id, document.workflow_id)
    await message.reply_text(
        CANCEL_MESSAGE_CANCELLED_TEMPLATE.format(workflow_id=document.workflow_id)
    )


@restricted
async def unhandled(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(UNHANDLED_MESSAGE)


@restricted
async def media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    telegram_user_id = update.effective_user.id

    seen_updates = context.bot_data["seen_updates"]
    if seen_updates.seen_before(update.update_id):
        logger.info("Skipping duplicate update_id=%s", update.update_id)
        return

    config = context.bot_data["config"]
    storage = context.bot_data["storage"]
    workflow_manager = context.bot_data["workflow_manager"]
    conversation_manager = context.bot_data["conversation_manager"]
    analysis_service = context.bot_data["analysis_service"]
    clarification_service = context.bot_data["clarification_service"]
    content_planning_service = context.bot_data["content_planning_service"]
    draft_generation_service = context.bot_data["draft_generation_service"]
    draft_version_manager = context.bot_data["draft_version_manager"]
    draft_manager = context.bot_data["draft_manager"]

    # docs/WORKFLOW.md §5: never silently start a second concurrent
    # instance — checked before any S3/Claude work for this new upload.
    try:
        existing = resolve_active_workflow(telegram_user_id, conversation_manager, workflow_manager)
    except NoActiveWorkflowError:
        existing = None
    except ConversationError as exc:
        logger.warning("Active-workflow check failed user_id=%s error=%s", telegram_user_id, exc)
        await message.reply_text(exc.user_message)
        return

    if existing is not None and existing.state not in TERMINAL_STATES:
        logger.info(
            "New media rejected, workflow already in progress user_id=%s workflow_id=%s state=%s",
            telegram_user_id,
            existing.workflow_id,
            existing.state.value,
        )
        await message.reply_text(
            ACTIVE_WORKFLOW_IN_PROGRESS_TEMPLATE.format(workflow_id=existing.workflow_id)
        )
        return

    try:
        result = await ingest_media(
            message,
            context.bot,
            telegram_user_id,
            config,
            storage,
            workflow_manager,
            conversation_manager,
        )
    except MediaIngestionError as exc:
        logger.warning(
            "Media ingestion rejected user_id=%s error=%s", telegram_user_id, exc
        )
        await message.reply_text(exc.user_message)
        return
    except Exception:
        logger.exception(
            "Unexpected error during media ingestion user_id=%s", telegram_user_id
        )
        await message.reply_text(MediaIngestionError.user_message)
        return

    await message.reply_text(
        MEDIA_SUCCESS_TEMPLATE.format(
            media_type=result.media_type.value,
            workflow_id=result.workflow_id,
        )
    )

    try:
        await analysis_service.analyze_workflow(
            workflow_id=result.workflow_id, telegram_user_id=telegram_user_id
        )
    except AnalysisError as exc:
        logger.warning(
            "Analysis failed workflow_id=%s user_id=%s error=%s",
            result.workflow_id,
            telegram_user_id,
            exc,
        )
        await message.reply_text(exc.user_message)
        return
    except Exception:
        logger.exception(
            "Unexpected error during analysis workflow_id=%s user_id=%s",
            result.workflow_id,
            telegram_user_id,
        )
        await message.reply_text(AnalysisError.user_message)
        return

    await _run_clarification_and_reply(
        clarification_service,
        content_planning_service,
        draft_generation_service,
        draft_version_manager,
        draft_manager,
        message,
        workflow_id=result.workflow_id,
        telegram_user_id=telegram_user_id,
    )


async def _run_clarification_and_reply(
    clarification_service,
    content_planning_service,
    draft_generation_service,
    draft_version_manager,
    draft_manager,
    message,
    *,
    workflow_id: str,
    telegram_user_id: int,
) -> None:
    """Shared by media() (right after a successful analysis) and retry()
    (re-attempting a stalled clarification-decision or analysis cycle):
    run one decision cycle and send the resulting question or "ready"
    message. On CONTINUE, chains straight into content planning — the
    user never needs a separate command for it. Never constructs a Claude
    prompt itself — only calls the services and renders their typed
    outcomes."""

    try:
        outcome = await clarification_service.evaluate_and_advance(
            workflow_id=workflow_id, telegram_user_id=telegram_user_id
        )
    except AnalysisError as exc:
        logger.warning(
            "Clarification evaluation failed workflow_id=%s user_id=%s error=%s",
            workflow_id,
            telegram_user_id,
            exc,
        )
        await message.reply_text(exc.user_message)
        return
    except Exception:
        logger.exception(
            "Unexpected error during clarification workflow_id=%s user_id=%s", workflow_id, telegram_user_id
        )
        await message.reply_text(AnalysisError.user_message)
        return

    await _reply_to_clarification_outcome(
        outcome,
        content_planning_service,
        draft_generation_service,
        draft_version_manager,
        draft_manager,
        message,
        workflow_id=workflow_id,
        telegram_user_id=telegram_user_id,
    )


async def _reply_to_clarification_outcome(
    outcome, content_planning_service, draft_generation_service, draft_version_manager, draft_manager, message,
    *, workflow_id: str, telegram_user_id: int
) -> None:
    """Shared by _run_clarification_and_reply() and text_reply(): send
    the question, or announce sufficiency and chain straight into content
    planning — the one place that decision is made, so both entry points
    behave identically."""

    if outcome.decision is ClarificationDecisionType.ASK_QUESTION:
        await message.reply_text(outcome.question_text)
        return

    await message.reply_text(CLARIFICATION_SUFFICIENT_MESSAGE)
    await _run_planning_and_reply(
        content_planning_service,
        draft_generation_service,
        draft_version_manager,
        draft_manager,
        message,
        workflow_id=workflow_id,
        telegram_user_id=telegram_user_id,
    )


async def _run_planning_and_reply(
    content_planning_service, draft_generation_service, draft_version_manager, draft_manager, message,
    *, workflow_id: str, telegram_user_id: int
) -> None:
    """Shared by _run_clarification_and_reply() (the normal automatic
    path) and retry()'s "content_planning" branch. Never displays the
    full plan — only a short, derived list of recommended outputs (see
    _format_plan_ready_message). On success, chains straight into
    generating the primary draft — the user never needs a separate
    command for it, exactly like the clarification -> planning chain
    above."""

    await message.reply_text(PLANNING_STARTED_MESSAGE)

    try:
        outcome = await content_planning_service.plan_content(
            workflow_id=workflow_id, telegram_user_id=telegram_user_id
        )
    except AnalysisError as exc:
        logger.warning(
            "Content planning failed workflow_id=%s user_id=%s error=%s", workflow_id, telegram_user_id, exc
        )
        await message.reply_text(exc.user_message)
        return
    except Exception:
        logger.exception(
            "Unexpected error during content planning workflow_id=%s user_id=%s", workflow_id, telegram_user_id
        )
        await message.reply_text(AnalysisError.user_message)
        return

    await message.reply_text(_format_plan_ready_message(outcome.outputs))
    await _run_draft_generation_and_reply(
        draft_generation_service, draft_version_manager, draft_manager, message,
        workflow_id=workflow_id, telegram_user_id=telegram_user_id,
    )


async def _run_draft_generation_and_reply(
    draft_generation_service, draft_version_manager, draft_manager, message, *, workflow_id: str, telegram_user_id: int
) -> None:
    """Shared by _run_planning_and_reply() (the normal automatic path) and
    retry()'s "primary_draft_generation" branch. Generates (or reuses)
    exactly the plan's priority-1 output and shows it as a review-ready
    preview — never implies it has been published anywhere.

    Immediately after a successful generation, resolves (and, on the very
    first draft, lazily migrates — see draft_version_manager.py) the
    Milestone 7 current-version pointer, so even the *original* draft
    always shows as "Version 1" with working review buttons from the
    start, not only after the first edit.

    Sending the preview happens strictly after generate_draft() has
    already persisted the draft and moved the workflow to
    SHOWING_PREVIEW. If the preview send itself fails, the draft and
    workflow state are left exactly as they are — no new Claude call, no
    FAILED transition, just a logged failure — since the user has already
    had their content generated and it remains available (e.g. via a
    future /status or re-send path), unlike a genuinely failed
    generation."""

    await message.reply_text(DRAFT_GENERATION_STARTED_MESSAGE)

    try:
        outcome = await draft_generation_service.generate_draft(
            workflow_id=workflow_id, telegram_user_id=telegram_user_id
        )
    except AnalysisError as exc:
        logger.warning(
            "Draft generation failed workflow_id=%s user_id=%s error=%s", workflow_id, telegram_user_id, exc
        )
        await message.reply_text(exc.user_message)
        return
    except Exception:
        logger.exception(
            "Unexpected error during draft generation workflow_id=%s user_id=%s", workflow_id, telegram_user_id
        )
        await message.reply_text(AnalysisError.user_message)
        return

    try:
        _, current_pointer, _ = resolve_current_draft(
            draft_version_manager=draft_version_manager, draft_manager=draft_manager,
            workflow_id=workflow_id, output_id=outcome.output_id,
        )
        version_number = current_pointer.current_version_number
    except AnalysisError:
        logger.exception(
            "Failed to resolve/migrate current-version pointer workflow_id=%s output_id=%s",
            workflow_id, outcome.output_id,
        )
        version_number = 1  # the draft was just freshly generated — it is version 1 either way

    await _send_draft_preview(
        message, workflow_id=workflow_id, output_type=outcome.output_type, content=outcome.content,
        version_number=version_number,
    )


@restricted
async def text_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    telegram_user_id = update.effective_user.id

    seen_updates = context.bot_data["seen_updates"]
    if seen_updates.seen_before(update.update_id):
        logger.info("Skipping duplicate update_id=%s", update.update_id)
        return

    conversation_manager = context.bot_data["conversation_manager"]
    workflow_manager = context.bot_data["workflow_manager"]
    clarification_service = context.bot_data["clarification_service"]
    content_planning_service = context.bot_data["content_planning_service"]
    draft_generation_service = context.bot_data["draft_generation_service"]
    draft_version_manager = context.bot_data["draft_version_manager"]
    draft_manager = context.bot_data["draft_manager"]
    draft_editing_service = context.bot_data["draft_editing_service"]

    try:
        document = resolve_active_workflow(telegram_user_id, conversation_manager, workflow_manager)
    except NoActiveWorkflowError:
        await message.reply_text(TEXT_REPLY_NO_ACTIVE_WORKFLOW_MESSAGE)
        return
    except ConversationError as exc:
        logger.warning("Text-reply resolution failed user_id=%s error=%s", telegram_user_id, exc)
        await message.reply_text(exc.user_message)
        return

    if document.state is WorkflowState.EDITING:
        await _handle_edit_instruction_reply(
            message, document, workflow_manager, draft_editing_service,
            telegram_user_id=telegram_user_id, telegram_update_id=update.update_id,
        )
        return

    if document.state is not WorkflowState.WAITING_FOR_USER:
        logger.info(
            "Text reply ignored, workflow not waiting user_id=%s workflow_id=%s state=%s",
            telegram_user_id,
            document.workflow_id,
            document.state.value,
        )
        await message.reply_text(TEXT_REPLY_NOT_WAITING_MESSAGE)
        return

    try:
        outcome = await clarification_service.handle_user_reply(
            workflow_id=document.workflow_id,
            telegram_user_id=telegram_user_id,
            reply_text=message.text,
            telegram_update_id=update.update_id,
        )
    except AnalysisError as exc:
        logger.warning(
            "Clarification reply handling failed workflow_id=%s user_id=%s error=%s",
            document.workflow_id,
            telegram_user_id,
            exc,
        )
        await message.reply_text(exc.user_message)
        return
    except Exception:
        logger.exception(
            "Unexpected error handling text reply workflow_id=%s user_id=%s",
            document.workflow_id,
            telegram_user_id,
        )
        await message.reply_text(AnalysisError.user_message)
        return

    if outcome is None:
        return  # duplicate reply — already recorded, nothing further to send

    await _reply_to_clarification_outcome(
        outcome,
        content_planning_service,
        draft_generation_service,
        draft_version_manager,
        draft_manager,
        message,
        workflow_id=document.workflow_id,
        telegram_user_id=telegram_user_id,
    )


async def _handle_edit_instruction_reply(
    message, document, workflow_manager, draft_editing_service, *, telegram_user_id: int, telegram_update_id: int
) -> None:
    """The next authorized text message while EDITING is treated as the
    edit instruction for the active workflow — never a new clarification
    question, never re-running analysis or planning. The instruction is
    persisted as a conversation turn *before* any Claude call (required
    for /retry to safely resume — see draft_editing_service.py), in the
    same atomic write that transitions EDITING -> GENERATING_CONTENT (see
    WorkflowManager.record_edit_instruction()). Once that write succeeds,
    a duplicate delivery of this same text update can never re-trigger
    this path again: text_reply() only reaches here while state is
    EDITING, and this write always leaves it in GENERATING_CONTENT."""

    pending_edit = document.pending_edit or {}
    operation_id = pending_edit.get("operation_id") or uuid.uuid4().hex
    turn = {
        "role": "user",
        "content": message.text,
        "turn_id": operation_id,
        "telegram_update_id": telegram_update_id,
    }
    updated_pending_edit = {**pending_edit, "status": "INSTRUCTION_RECEIVED", "instruction_turn_id": operation_id}

    try:
        workflow_manager.record_edit_instruction(document.workflow_id, turn=turn, pending_edit=updated_pending_edit)
    except WorkflowError as exc:
        logger.warning(
            "Failed to record edit instruction workflow_id=%s user_id=%s error=%s",
            document.workflow_id, telegram_user_id, exc,
        )
        await message.reply_text(exc.user_message)
        return

    await _run_draft_edit_and_reply(
        draft_editing_service, message, workflow_id=document.workflow_id, telegram_user_id=telegram_user_id
    )


async def _run_draft_edit_and_reply(draft_editing_service, message, *, workflow_id: str, telegram_user_id: int) -> None:
    """Shared by _handle_edit_instruction_reply() (the normal path,
    right after the instruction is persisted) and retry()'s
    "draft_editing" branch (resuming using the already-persisted
    instruction — never asks the user to retype it)."""

    await message.reply_text(EDIT_PROCESSING_MESSAGE)

    try:
        outcome = await draft_editing_service.edit_draft(
            workflow_id=workflow_id, telegram_user_id=telegram_user_id
        )
    except AnalysisError as exc:
        logger.warning(
            "Draft edit failed workflow_id=%s user_id=%s error=%s", workflow_id, telegram_user_id, exc
        )
        await message.reply_text(exc.user_message)
        return
    except Exception:
        logger.exception(
            "Unexpected error during draft edit workflow_id=%s user_id=%s", workflow_id, telegram_user_id
        )
        await message.reply_text(AnalysisError.user_message)
        return

    await _send_draft_preview(
        message, workflow_id=workflow_id, output_type=outcome.output_type, content=outcome.content,
        version_number=outcome.version_number,
    )


@restricted
async def retry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    telegram_user_id = update.effective_user.id
    conversation_manager = context.bot_data["conversation_manager"]
    workflow_manager = context.bot_data["workflow_manager"]
    analysis_service = context.bot_data["analysis_service"]
    clarification_service = context.bot_data["clarification_service"]
    content_planning_service = context.bot_data["content_planning_service"]
    draft_generation_service = context.bot_data["draft_generation_service"]
    draft_version_manager = context.bot_data["draft_version_manager"]
    draft_manager = context.bot_data["draft_manager"]
    draft_editing_service = context.bot_data["draft_editing_service"]
    publication_preparation_service = context.bot_data["publication_preparation_service"]
    execution_service = context.bot_data["execution_service"]
    execution_dispatch_service = context.bot_data["execution_dispatch_service"]
    instagram_publishing_enabled = context.bot_data["config"].instagram_publishing_enabled

    try:
        document = resolve_active_workflow(telegram_user_id, conversation_manager, workflow_manager)
    except NoActiveWorkflowError:
        await message.reply_text(RETRY_MESSAGE_NOTHING_TO_RETRY)
        return
    except ConversationError as exc:
        logger.warning("Retry resolution failed user_id=%s error=%s", telegram_user_id, exc)
        await message.reply_text(exc.user_message)
        return

    pending_retry = document.metadata.get("pending_retry")
    if pending_retry is None:
        await message.reply_text(RETRY_MESSAGE_NOTHING_TO_RETRY)
        return

    logger.info(
        "Retry requested user_id=%s workflow_id=%s pending_retry=%s",
        telegram_user_id,
        document.workflow_id,
        pending_retry,
    )

    if pending_retry == "analysis":
        try:
            await analysis_service.analyze_workflow(
                workflow_id=document.workflow_id, telegram_user_id=telegram_user_id
            )
        except AnalysisError as exc:
            await message.reply_text(exc.user_message)
            return
        except Exception:
            logger.exception("Unexpected error during retried analysis workflow_id=%s", document.workflow_id)
            await message.reply_text(AnalysisError.user_message)
            return
        await _run_clarification_and_reply(
            clarification_service,
            content_planning_service,
            draft_generation_service,
            draft_version_manager,
            draft_manager,
            message,
            workflow_id=document.workflow_id,
            telegram_user_id=telegram_user_id,
        )
        return

    if pending_retry == "clarification_decision":
        await _run_clarification_and_reply(
            clarification_service,
            content_planning_service,
            draft_generation_service,
            draft_version_manager,
            draft_manager,
            message,
            workflow_id=document.workflow_id,
            telegram_user_id=telegram_user_id,
        )
        return

    if pending_retry == "content_planning":
        await _run_planning_and_reply(
            content_planning_service,
            draft_generation_service,
            draft_version_manager,
            draft_manager,
            message,
            workflow_id=document.workflow_id,
            telegram_user_id=telegram_user_id,
        )
        return

    if pending_retry == "primary_draft_generation":
        await _run_draft_generation_and_reply(
            draft_generation_service, draft_version_manager, draft_manager, message,
            workflow_id=document.workflow_id, telegram_user_id=telegram_user_id,
        )
        return

    if pending_retry == "draft_editing":
        await _run_draft_edit_and_reply(
            draft_editing_service, message, workflow_id=document.workflow_id, telegram_user_id=telegram_user_id
        )
        return

    if pending_retry == "publication_preparation":
        # Resumes using the workflow_id already resolved via the active
        # pointer above — never reapproves the draft, never asks the user
        # to review it again (the pointer stays active specifically so
        # this branch can find it; see _handle_approve_action() and
        # src/publication/service.py's module docstring).
        await _run_publication_preparation_and_reply(
            publication_preparation_service, execution_service, message,
            workflow_id=document.workflow_id, telegram_user_id=telegram_user_id,
            instagram_publishing_enabled=instagram_publishing_enabled,
        )
        return

    if pending_retry == "publication_execution":
        # Publication preparation already succeeded in an earlier request
        # (the pointer stayed active specifically for this) — only
        # execution-record creation is pending. Never re-runs preparation,
        # never calls Claude, never reapproves the draft.
        await _run_execution_creation_and_reply(
            execution_service, message, workflow_id=document.workflow_id, telegram_user_id=telegram_user_id,
            instagram_publishing_enabled=instagram_publishing_enabled,
        )
        return

    if pending_retry == "publication_dispatch":
        # An explicit Publish action already authorized this execution in
        # an earlier request — /retry never creates a fresh authorization,
        # only resumes an already-authorized dispatch. Never calls Claude,
        # never recreates the package or execution.
        await _run_dispatch_and_reply(
            execution_dispatch_service, message, publication_id=(document.publication or {}).get("publication_id"),
            telegram_user_id=telegram_user_id, use_retry_entry_point=True,
        )
        return

    logger.warning(
        "Unrecognized pending_retry value user_id=%s workflow_id=%s pending_retry=%r",
        telegram_user_id,
        document.workflow_id,
        pending_retry,
    )
    await message.reply_text(AnalysisError.user_message)


# --- Milestone 7: review-action callback handling -------------------------


def _make_generated_draft_reference(document, pointer, *, status: ReviewStatus, extra: dict | None = None) -> dict:
    """The one place a review action builds the workflow's lightweight
    `generated_draft` reference — never the draft body/hashtags/CTA,
    never the full edit instruction, never version history; just enough
    to describe "which version, what review status" (see
    WorkflowManager.approve_draft()/save_draft()/reject_draft())."""

    reference = {
        "draft_id": document.draft_id,
        "output_id": document.output_id,
        "output_type": document.output_type,
        "current_version": pointer.current_version_number,
        "status": status.value,
        "schema_version": document.schema_version,
        "updated_at": _now_iso(),
    }
    if extra:
        reference.update(extra)
    return reference


def _is_repeat_of_completed_action(document, pointer, callback_version: int, *, expected_state, expected_status) -> bool:
    """A review action is an idempotent repeat only if the workflow is
    ALREADY in the exact state/status this action would produce, for the
    EXACT version the callback names — never a looser match, so a stale
    button from an older, different version is never mistaken for a
    harmless repeat (see StaleDraftVersionError's callers)."""

    return (
        document.state is expected_state
        and pointer.status is expected_status
        and pointer.current_version_number == callback_version
    )


@restricted
async def review_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point for every inline review button (Approve / Edit / Save
    Draft / Reject). Callback data is `review:<action>:<workflow_id>:
    <version_number>` (see _build_review_keyboard()/_parse_review_callback_data()).

    Ownership is verified the same way every other command already
    verifies "whose workflow is this" — by resolving the caller's OWN
    active-conversation pointer (resolve_active_workflow()), never by
    trusting the workflow_id embedded in the callback data as the primary
    lookup. If the resolved workflow's ID doesn't match the callback's,
    this is either someone else's button or a stale reference to an
    already-finished workflow — treated identically to a stale version,
    with zero side effects."""

    query = update.callback_query
    telegram_user_id = update.effective_user.id

    seen_updates = context.bot_data["seen_updates"]
    if seen_updates.seen_before(update.update_id):
        logger.info("Skipping duplicate callback update_id=%s", update.update_id)
        await query.answer()
        return

    await query.answer()

    parsed = _parse_review_callback_data(query.data)
    if parsed is None:
        logger.warning("Malformed review callback_data user_id=%s data=%r", telegram_user_id, query.data)
        return
    action, callback_workflow_id, callback_version = parsed

    conversation_manager = context.bot_data["conversation_manager"]
    workflow_manager = context.bot_data["workflow_manager"]
    draft_manager = context.bot_data["draft_manager"]
    draft_version_manager = context.bot_data["draft_version_manager"]
    publication_preparation_service = context.bot_data["publication_preparation_service"]
    execution_service = context.bot_data["execution_service"]
    instagram_publishing_enabled = context.bot_data["config"].instagram_publishing_enabled

    try:
        document = resolve_active_workflow(telegram_user_id, conversation_manager, workflow_manager)
    except NoActiveWorkflowError:
        await query.message.reply_text(REVIEW_ACTION_NO_ACTIVE_WORKFLOW_MESSAGE)
        return
    except ConversationError as exc:
        logger.warning("Review-action resolution failed user_id=%s error=%s", telegram_user_id, exc)
        await query.message.reply_text(exc.user_message)
        return

    if document.workflow_id != callback_workflow_id:
        logger.info(
            "Review action against a foreign/stale workflow reference user_id=%s callback_workflow_id=%s "
            "active_workflow_id=%s",
            telegram_user_id, callback_workflow_id, document.workflow_id,
        )
        await query.message.reply_text(STALE_ACTION_MESSAGE)
        return

    output_id = (document.generated_draft or {}).get("output_id")
    if output_id is None:
        await query.message.reply_text(REVIEW_ACTION_UNAVAILABLE_MESSAGE)
        return

    try:
        current_document, current_pointer, pointer_etag = resolve_current_draft(
            draft_version_manager=draft_version_manager, draft_manager=draft_manager,
            workflow_id=document.workflow_id, output_id=output_id,
        )
    except AnalysisError as exc:
        logger.warning(
            "Failed to resolve current draft for review action workflow_id=%s error=%s", document.workflow_id, exc
        )
        await query.message.reply_text(exc.user_message)
        return

    if action == "approve":
        await _handle_approve_action(
            query, document, current_document, current_pointer, callback_version,
            workflow_manager=workflow_manager, draft_version_manager=draft_version_manager,
            telegram_user_id=telegram_user_id, conversation_manager=conversation_manager,
            publication_preparation_service=publication_preparation_service, execution_service=execution_service,
            instagram_publishing_enabled=instagram_publishing_enabled,
        )
    elif action == "save":
        await _handle_save_action(
            query, document, current_document, current_pointer, callback_version,
            workflow_manager=workflow_manager, draft_version_manager=draft_version_manager,
            telegram_user_id=telegram_user_id, conversation_manager=conversation_manager,
        )
    elif action == "reject":
        await _handle_reject_action(
            query, document, current_document, current_pointer, callback_version,
            workflow_manager=workflow_manager, draft_version_manager=draft_version_manager,
            telegram_user_id=telegram_user_id, conversation_manager=conversation_manager,
        )
    elif action == "edit":
        await _handle_edit_entry_action(
            query, document, current_document, current_pointer, callback_version,
            workflow_manager=workflow_manager,
        )


async def _reject_stale(query, current_document, current_pointer) -> None:
    await query.message.reply_text(STALE_ACTION_MESSAGE)
    await _send_draft_preview(
        query.message, workflow_id=current_document.workflow_id, output_type=current_document.output_type,
        content=current_document.content or {}, version_number=current_pointer.current_version_number,
    )


async def _handle_approve_action(
    query, document, current_document, current_pointer, callback_version, *,
    workflow_manager, draft_version_manager, telegram_user_id, conversation_manager,
    publication_preparation_service, execution_service, instagram_publishing_enabled,
) -> None:
    if _is_repeat_of_completed_action(
        document, current_pointer, callback_version, expected_state=WorkflowState.COMPLETED, expected_status=ReviewStatus.APPROVED
    ):
        # Approval itself is already durable and is never repeated — this
        # is just a duplicate button click. Describe whatever publication-
        # preparation outcome already exists (if any) rather than
        # re-running preparation, mirroring every other repeat-action
        # branch's "zero side effects" rule.
        await query.message.reply_text(_describe_publication_outcome(document))
        return

    if document.state is not WorkflowState.SHOWING_PREVIEW or current_pointer.current_version_number != callback_version:
        await _reject_stale(query, current_document, current_pointer)
        return

    approved_at = _now_iso()
    output_id = current_document.output_id
    reference = _make_generated_draft_reference(
        current_document, current_pointer, status=ReviewStatus.APPROVED,
        extra={"approved_at": approved_at, "approved_by_telegram_user_id": telegram_user_id},
    )
    try:
        draft_version_manager.update_pointer_status(
            workflow_id=document.workflow_id, output_id=output_id, status=ReviewStatus.APPROVED,
            approved_at=approved_at, approved_by_telegram_user_id=telegram_user_id,
        )
        workflow_manager.approve_draft(document.workflow_id, generated_draft_reference=reference)
    except (WorkflowError, AnalysisError) as exc:
        await query.message.reply_text(exc.user_message)
        return

    logger.info(
        "Draft approved workflow_id=%s user_id=%s version=%s", document.workflow_id, telegram_user_id, callback_version
    )
    # Publication preparation runs synchronously right after approval,
    # but is a separate domain write — approval above is already durable
    # and is never reverted regardless of how preparation goes. The
    # active pointer is deliberately NOT cleared here; ownership of that
    # moves into the preparation call itself (see its module docstring in
    # src/publication/service.py for why) — and, in turn, into the
    # execution-creation step chained after it (Milestone 9).
    await _run_publication_preparation_and_reply(
        publication_preparation_service, execution_service, query.message,
        workflow_id=document.workflow_id, telegram_user_id=telegram_user_id,
        instagram_publishing_enabled=instagram_publishing_enabled,
    )


def _describe_publication_outcome(document) -> str:
    """Used both for a duplicate Approve click (already-approved workflow)
    and could be reused by /status — describes the publication-preparation
    outcome using only the workflow's own lightweight `publication`
    reference and `pending_retry` marker, never re-running preparation or
    reaching into the publications/ store directly."""

    publication = document.publication or {}
    if publication.get("status") == "READY_FOR_PUBLISHING":
        return APPROVE_AND_PREPARED_MESSAGE
    if publication.get("status") == "FAILED":
        return APPROVE_PREPARATION_UNAVAILABLE_MESSAGE
    if document.metadata.get("pending_retry") == "publication_preparation":
        return APPROVE_PREPARATION_RETRY_MESSAGE
    # Preparation runs synchronously immediately after approval, so this
    # should be unreachable in practice — a safe fallback in case it's
    # ever reached (e.g. a very tight race with a concurrent /retry).
    return APPROVE_PREPARATION_RETRY_MESSAGE


async def _run_publication_preparation_and_reply(
    publication_preparation_service, execution_service, message, *, workflow_id: str, telegram_user_id: int,
    instagram_publishing_enabled: bool,
) -> None:
    """Shared by _handle_approve_action() (right after approval succeeds)
    and retry()'s "publication_preparation" branch. Never calls Claude;
    never publishes; approval itself is never affected by how this goes.

    On success, chains directly into execution-record creation
    (Milestone 9, `_run_execution_creation_and_reply()`) — pointer-
    clearing is deferred all the way to that step (see
    src/publication/service.py and src/execution/service.py's module
    docstrings), so this call passes `clear_pointer_on_success=False`."""

    try:
        await publication_preparation_service.prepare_publication(
            workflow_id=workflow_id, telegram_user_id=telegram_user_id, clear_pointer_on_success=False,
        )
    except PublicationPermanentError as exc:
        logger.warning(
            "Publication preparation permanently failed workflow_id=%s error=%s", workflow_id, exc
        )
        await message.reply_text(APPROVE_PREPARATION_UNAVAILABLE_MESSAGE)
        return
    except PublicationError as exc:
        logger.warning(
            "Publication preparation retryable failure workflow_id=%s error=%s", workflow_id, exc
        )
        await message.reply_text(APPROVE_PREPARATION_RETRY_MESSAGE)
        return
    except Exception:
        logger.exception("Unexpected error during publication preparation workflow_id=%s", workflow_id)
        await message.reply_text(APPROVE_PREPARATION_RETRY_MESSAGE)
        return

    await _run_execution_creation_and_reply(
        execution_service, message, workflow_id=workflow_id, telegram_user_id=telegram_user_id,
        instagram_publishing_enabled=instagram_publishing_enabled,
    )


async def _run_execution_creation_and_reply(
    execution_service, message, *, workflow_id: str, telegram_user_id: int, instagram_publishing_enabled: bool,
) -> None:
    """Shared by _run_publication_preparation_and_reply() (chained right
    after a successful preparation) and retry()'s "publication_execution"
    branch (resuming a preparation that already succeeded earlier). Never
    calls Claude; never calls any platform API.

    Execution-creation failure is deliberately not surfaced as a distinct
    Telegram outcome: the publication package is already genuinely
    prepared regardless of how execution-record creation goes, so the
    reply is always APPROVE_AND_PREPARED_MESSAGE — a failure here is
    logged and left for `/retry` to resolve silently.

    Milestone 10: when Instagram publishing is enabled, a successful
    execution creation attaches an explicit "Publish to Instagram" button
    (never automatic — see README.md's "User trigger and explicit
    authorization") and defers pointer-clearing one link further, to the
    dispatch step, exactly mirroring the approve -> prepare -> execute
    deferral chain."""

    outcome = None
    try:
        outcome = await execution_service.create_execution(
            workflow_id=workflow_id, telegram_user_id=telegram_user_id,
            clear_pointer_on_success=not instagram_publishing_enabled,
        )
    except ExecutionError as exc:
        logger.warning("Execution creation failed workflow_id=%s error=%s", workflow_id, exc)
    except Exception:
        logger.exception("Unexpected error during execution creation workflow_id=%s", workflow_id)

    if outcome is not None and instagram_publishing_enabled:
        await message.reply_text(
            APPROVE_AND_PREPARED_MESSAGE, reply_markup=_build_dispatch_keyboard(outcome.publication_id)
        )
        return

    await message.reply_text(APPROVE_AND_PREPARED_MESSAGE)


@restricted
async def dispatch_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point for the explicit "Publish to Instagram" inline button
    (`dispatch:instagram:<publication_id>` — see `_build_dispatch_keyboard()`).

    Ownership is verified the same way review_action() already verifies
    it: never by trusting the workflow/execution named in the callback
    data as the primary lookup, but by loading the execution's own
    workflow (via `src/execution/dispatch_service.py`'s
    `ExecutionDispatchService`, which loads the workflow and checks
    `telegram_user_id` itself) — the active-workflow pointer is *also*
    still resolvable here (Milestone 10 defers pointer-clearing through
    the whole publish-pending window when publishing is enabled), so an
    additional, defense-in-depth ownership check happens via the same
    `resolve_active_workflow()` pattern every other review action uses."""

    query = update.callback_query
    telegram_user_id = update.effective_user.id

    seen_updates = context.bot_data["seen_updates"]
    if seen_updates.seen_before(update.update_id):
        logger.info("Skipping duplicate dispatch callback update_id=%s", update.update_id)
        await query.answer()
        return

    await query.answer()

    config = context.bot_data["config"]
    if not config.instagram_publishing_enabled:
        await query.message.reply_text(INSTAGRAM_PUBLISHING_DISABLED_MESSAGE)
        return

    parsed = _parse_dispatch_callback_data(query.data)
    if parsed is None:
        logger.warning("Malformed dispatch callback_data user_id=%s data=%r", telegram_user_id, query.data)
        return
    _, publication_id = parsed

    execution_dispatch_service = context.bot_data["execution_dispatch_service"]
    await _run_dispatch_and_reply(
        execution_dispatch_service, query.message, publication_id=publication_id,
        telegram_user_id=telegram_user_id, use_retry_entry_point=False,
    )


async def _run_dispatch_and_reply(
    execution_dispatch_service, message, *, publication_id: str | None, telegram_user_id: int, use_retry_entry_point: bool,
) -> None:
    """Shared by dispatch_action() (the explicit "Publish to Instagram"
    button — creates a fresh authorization if none exists) and retry()'s
    "publication_dispatch" branch (`use_retry_entry_point=True` — never
    creates a fresh authorization, only resumes one that already exists).
    Never constructs a Graph API request, never inspects an access token,
    never polls container status directly, never updates execution status
    directly, and never decides retryability itself — all of that is
    `ExecutionDispatchService`'s job."""

    if not publication_id:
        await message.reply_text(REVIEW_ACTION_UNAVAILABLE_MESSAGE)
        return

    await message.reply_text(DISPATCH_STARTED_MESSAGE)

    try:
        if use_retry_entry_point:
            outcome = await execution_dispatch_service.retry_dispatch(
                publication_id=publication_id, telegram_user_id=telegram_user_id
            )
        else:
            outcome = await execution_dispatch_service.authorize_and_dispatch(
                publication_id=publication_id, telegram_user_id=telegram_user_id
            )
    except UnauthorizedDispatchError:
        logger.warning(
            "Unauthorized dispatch attempt user_id=%s publication_id=%s", telegram_user_id, publication_id
        )
        await message.reply_text(REVIEW_ACTION_UNAVAILABLE_MESSAGE)
        return
    except ExecutionError as exc:
        logger.warning("Dispatch failed publication_id=%s error=%s", publication_id, exc)
        await message.reply_text(exc.user_message)
        return
    except Exception:
        logger.exception("Unexpected error during dispatch publication_id=%s", publication_id)
        await message.reply_text(AnalysisError.user_message)
        return

    await message.reply_text(_describe_dispatch_outcome(outcome))


async def _handle_save_action(
    query, document, current_document, current_pointer, callback_version, *,
    workflow_manager, draft_version_manager, telegram_user_id, conversation_manager,
) -> None:
    if _is_repeat_of_completed_action(
        document, current_pointer, callback_version,
        expected_state=WorkflowState.SAVED_AS_DRAFT, expected_status=ReviewStatus.SAVED_AS_DRAFT,
    ):
        await query.message.reply_text(SAVE_DRAFT_SUCCESS_MESSAGE)
        return

    if document.state is not WorkflowState.SHOWING_PREVIEW or current_pointer.current_version_number != callback_version:
        await _reject_stale(query, current_document, current_pointer)
        return

    output_id = current_document.output_id
    reference = _make_generated_draft_reference(current_document, current_pointer, status=ReviewStatus.SAVED_AS_DRAFT)
    try:
        draft_version_manager.update_pointer_status(
            workflow_id=document.workflow_id, output_id=output_id, status=ReviewStatus.SAVED_AS_DRAFT
        )
        workflow_manager.save_draft(document.workflow_id, generated_draft_reference=reference)
    except (WorkflowError, AnalysisError) as exc:
        await query.message.reply_text(exc.user_message)
        return

    clear_pointer_if_terminal(
        telegram_user_id=telegram_user_id, workflow_state=WorkflowState.SAVED_AS_DRAFT,
        conversation_manager=conversation_manager,
    )
    logger.info("Draft saved workflow_id=%s version=%s", document.workflow_id, callback_version)
    await query.message.reply_text(SAVE_DRAFT_SUCCESS_MESSAGE)


async def _handle_reject_action(
    query, document, current_document, current_pointer, callback_version, *,
    workflow_manager, draft_version_manager, telegram_user_id, conversation_manager,
) -> None:
    if _is_repeat_of_completed_action(
        document, current_pointer, callback_version, expected_state=WorkflowState.REJECTED, expected_status=ReviewStatus.REJECTED
    ):
        await query.message.reply_text(REJECT_SUCCESS_MESSAGE)
        return

    if document.state is not WorkflowState.SHOWING_PREVIEW or current_pointer.current_version_number != callback_version:
        await _reject_stale(query, current_document, current_pointer)
        return

    output_id = current_document.output_id
    reference = _make_generated_draft_reference(current_document, current_pointer, status=ReviewStatus.REJECTED)
    try:
        draft_version_manager.update_pointer_status(
            workflow_id=document.workflow_id, output_id=output_id, status=ReviewStatus.REJECTED
        )
        workflow_manager.reject_draft(document.workflow_id, generated_draft_reference=reference)
    except (WorkflowError, AnalysisError) as exc:
        await query.message.reply_text(exc.user_message)
        return

    clear_pointer_if_terminal(
        telegram_user_id=telegram_user_id, workflow_state=WorkflowState.REJECTED,
        conversation_manager=conversation_manager,
    )
    logger.info("Draft rejected workflow_id=%s version=%s", document.workflow_id, callback_version)
    await query.message.reply_text(REJECT_SUCCESS_MESSAGE)


async def _handle_edit_entry_action(
    query, document, current_document, current_pointer, callback_version, *, workflow_manager,
) -> None:
    if (
        document.state is WorkflowState.EDITING
        and document.pending_edit
        and document.pending_edit.get("expected_parent_version") == callback_version
    ):
        # Duplicate "Edit" callback delivery — already awaiting this exact
        # version's instruction; just re-prompt, no new operation_id.
        await query.message.reply_text(EDIT_MODE_PROMPT_MESSAGE)
        return

    if document.state is not WorkflowState.SHOWING_PREVIEW or current_pointer.current_version_number != callback_version:
        await _reject_stale(query, current_document, current_pointer)
        return

    operation_id = uuid.uuid4().hex
    pending_edit = {
        "output_id": current_document.output_id,
        "operation_id": operation_id,
        "expected_parent_version": callback_version,
        "status": "AWAITING_INSTRUCTION",
        "instruction_turn_id": None,
        "completed_version_number": None,
        "requested_at": _now_iso(),
    }
    try:
        workflow_manager.enter_editing(document.workflow_id, pending_edit=pending_edit)
    except WorkflowError as exc:
        await query.message.reply_text(exc.user_message)
        return

    await query.message.reply_text(EDIT_MODE_PROMPT_MESSAGE)


@restricted
async def instagram_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Milestone 11A: `/instagram_status` — read-only Instagram
    operational diagnostics. Deliberately requires no active workflow
    (unlike /status/cancel/retry): this reports the *system's* Instagram
    readiness, not anything about a specific post. Creates no execution
    or publication record, touches no workflow document, and never calls
    a write Meta operation (create_media_container/publish_media) — see
    src/publisher/instagram/diagnostics.py's module docstring for the
    exact read-only sequence and the three independent health concepts
    (application healthy / Instagram configured / Instagram ready) this
    reports without ever conflating them. Works, and is always safe to
    call, whether or not Instagram publishing is enabled."""

    diagnostics_service = context.bot_data["instagram_diagnostics_service"]
    result = diagnostics_service.check_status()
    await update.effective_message.reply_text(_format_instagram_status_message(result))


def _is_authorized_live_test_operator(context: ContextTypes.DEFAULT_TYPE, telegram_user_id: int) -> bool:
    config = context.bot_data["config"]
    return is_live_test_operator(telegram_user_id, config.instagram_live_test_operator_ids)


async def _send_live_validation_preview(
    live_validation_service, message, *, telegram_user_id: int, publication_id: str
) -> None:
    """Shared by instagram_test_publish() (the single-candidate case) and
    instagram_live_validation_action()'s "s" (select) branch — always
    re-resolves and re-validates eligibility fresh via `build_preview()`,
    never trusts a previously-shown candidate list. Persists the durable
    AWAITING_CONFIRMATION state as a side effect of building the preview
    — see live_validation.py's own docstring for why."""

    try:
        preview = live_validation_service.build_preview(
            telegram_user_id=telegram_user_id, publication_id=publication_id
        )
    except NotLiveTestOperatorError:
        await message.reply_text(LIVE_VALIDATION_NOT_OPERATOR_MESSAGE)
        return
    except LiveValidationError as exc:
        await message.reply_text(exc.user_message)
        return
    except Exception:
        logger.exception(
            "Unexpected error building live-validation preview publication_id=%s", publication_id
        )
        await message.reply_text(LIVE_VALIDATION_UNAVAILABLE_MESSAGE)
        return

    await message.reply_text(
        _format_live_validation_preview_message(preview),
        reply_markup=_build_live_validation_confirmation_keyboard(preview.publication_id, preview.confirmation_token),
    )


@restricted
async def instagram_test_publish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Milestone 11B: `/instagram_test_publish` — the entry point for the
    first controlled, operator-only, explicitly-confirmed Instagram live
    validation. Never publishes anything itself: it only locates an
    eligible execution (via the existing ownership-safe active-workflow
    pointer — see live_validation.py's own docstring for why this never
    scans S3 directly) and renders a confirmation preview. The actual
    dispatch only ever happens after a second, distinct "Confirm live
    publication" callback (`instagram_live_validation_action()` below),
    which re-validates everything again before calling the exact same
    `ExecutionDispatchService.authorize_and_dispatch()` the ordinary
    "Publish to Instagram" button already calls.

    Gated by two independent checks, both required: the general
    `@restricted` Telegram allowlist (already applied by the decorator),
    and `INSTAGRAM_LIVE_TEST_OPERATOR_IDS` membership (checked below) —
    an ordinary authorized bot user is never automatically trusted to
    perform this."""

    message = update.effective_message
    telegram_user_id = update.effective_user.id
    config = context.bot_data["config"]

    if not config.instagram_publishing_enabled:
        await message.reply_text(INSTAGRAM_PUBLISHING_DISABLED_MESSAGE)
        return
    if not _is_authorized_live_test_operator(context, telegram_user_id):
        await message.reply_text(LIVE_VALIDATION_NOT_OPERATOR_MESSAGE)
        return

    live_validation_service = context.bot_data["live_validation_service"]

    try:
        candidates, reason = live_validation_service.find_eligible_candidates(telegram_user_id=telegram_user_id)
    except NotLiveTestOperatorError:
        await message.reply_text(LIVE_VALIDATION_NOT_OPERATOR_MESSAGE)
        return
    except Exception:
        logger.exception("Unexpected error finding live-validation candidates user_id=%s", telegram_user_id)
        await message.reply_text(LIVE_VALIDATION_UNAVAILABLE_MESSAGE)
        return

    if not candidates:
        await message.reply_text(
            LIVE_VALIDATION_NO_CANDIDATES_TEMPLATE.format(reason=reason or "No eligible publication was found.")
        )
        return

    if len(candidates) > 1:
        # Structurally unreachable under the current one-active-workflow-
        # per-operator architecture (see live_validation.py's own
        # docstring) — implemented for forward compatibility, never a
        # list position as the durable identity.
        await message.reply_text(
            LIVE_VALIDATION_MULTIPLE_CANDIDATES_MESSAGE, reply_markup=_build_live_validation_selection_keyboard(candidates)
        )
        return

    await _send_live_validation_preview(
        live_validation_service, message, telegram_user_id=telegram_user_id, publication_id=candidates[0].publication_id
    )


@restricted
async def instagram_live_validation_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point for the three controlled-live-validation inline
    buttons: select (multiple-candidate case), "Confirm live
    publication", and "Cancel" (`ilv:s:...` / `ilv:c:...` / `ilv:x:...` —
    see `_parse_live_validation_callback_data()`). A distinct callback
    namespace from `dispatch:...` (dispatch_action() above) — the two
    must never be confused. Every branch re-verifies operator
    authorization independently of the Telegram-layer check, exactly
    like `ControlledLiveValidationService` itself does."""

    query = update.callback_query
    telegram_user_id = update.effective_user.id

    seen_updates = context.bot_data["seen_updates"]
    if seen_updates.seen_before(update.update_id):
        logger.info("Skipping duplicate live-validation callback update_id=%s", update.update_id)
        await query.answer()
        return

    await query.answer()

    if not _is_authorized_live_test_operator(context, telegram_user_id):
        await query.message.reply_text(LIVE_VALIDATION_NOT_OPERATOR_MESSAGE)
        return

    parsed = _parse_live_validation_callback_data(query.data)
    if parsed is None:
        logger.warning("Malformed live-validation callback_data user_id=%s data=%r", telegram_user_id, query.data)
        return
    action, publication_id, token = parsed

    live_validation_service = context.bot_data["live_validation_service"]

    if action == "s":
        await _send_live_validation_preview(
            live_validation_service, query.message, telegram_user_id=telegram_user_id, publication_id=publication_id
        )
        return

    if action == "x":
        try:
            live_validation_service.cancel(
                telegram_user_id=telegram_user_id, publication_id=publication_id, confirmation_token=token
            )
        except NotLiveTestOperatorError:
            await query.message.reply_text(LIVE_VALIDATION_NOT_OPERATOR_MESSAGE)
            return
        except Exception:
            logger.exception("Unexpected error cancelling live validation publication_id=%s", publication_id)
        await query.message.reply_text(LIVE_VALIDATION_CANCELLED_MESSAGE)
        return

    # action == "c": the one, distinct, explicit confirmation action.
    await query.message.reply_text(DISPATCH_STARTED_MESSAGE)
    try:
        outcome = await live_validation_service.confirm(
            telegram_user_id=telegram_user_id, publication_id=publication_id, confirmation_token=token
        )
    except NotLiveTestOperatorError:
        await query.message.reply_text(LIVE_VALIDATION_NOT_OPERATOR_MESSAGE)
        return
    except LiveValidationError as exc:
        await query.message.reply_text(exc.user_message)
        return
    except ExecutionError as exc:
        logger.warning("Controlled live-validation dispatch failed publication_id=%s error=%s", publication_id, exc)
        await query.message.reply_text(exc.user_message)
        return
    except Exception:
        logger.exception(
            "Unexpected error during controlled live-validation dispatch publication_id=%s", publication_id
        )
        await query.message.reply_text(AnalysisError.user_message)
        return

    await query.message.reply_text(_describe_live_validation_outcome(outcome))
