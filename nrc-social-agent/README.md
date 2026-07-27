# NRC Social Agent

Private, AI-powered social-media publishing assistant for NRC. See the planning
documents for full context — this README only covers local setup and running
this milestone's code:

- [CLAUDE.md](CLAUDE.md) — permanent project context
- [PRODUCT.md](PRODUCT.md) — product vision and Version 1 scope
- [ARCHITECTURE.md](ARCHITECTURE.md) — system architecture
- [ROADMAP.md](ROADMAP.md) — phased milestones
- [DECISIONS.md](DECISIONS.md) — approved architectural decisions
- [docs/WORKFLOW.md](docs/WORKFLOW.md) — full Version 1 behavioral specification

## Current milestone

**Phase 1 / Milestone 11D.1 — Deploy Only `nrc-social-agent/` to
JustRunMy.App.** This repository is a monorepo with three independent
projects sharing one `main` branch. `.github/workflows/deploy-social-agent.yml`
(at the monorepo root) now uses Git's native
`git subtree split --prefix=nrc-social-agent` to push **only** this
project's own content to JustRunMy.App, with that content at the
deployment repository's *root* — never the whole monorepo, never a
nested `nrc-social-agent/` subdirectory, and never "NRC Website/" or
"nrc-ai-agents/". Both a dry run and a real run generate and validate
this subtree commit (confirming `Dockerfile`/`requirements.txt`/`src`
are present at its root and the sibling projects are absent); only the
actual push to JustRunMy.App is skipped on a dry run. **No real
deployment has been run through it yet** — see
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#github-actions-deployment-pipeline-milestones-11d-11d1)
for the full setup and first-run walkthrough, including a verified (not
assumed) explanation of why ordinary forward deployments fast-forward
safely without `--force`.

Deployment secrets moved from three separate values
(`JUSTRUNMYAPP_GIT_USERNAME`/`_PASSWORD`/`_REPO`) to a single
`JUSTRUNMYAPP_GIT_URL` secret holding the complete, already-valid deploy
URL — safer, since the workflow never has to construct or URL-encode a
credential-bearing URL itself. The workflow remains deliberately,
structurally independent of Instagram operational validation: it never
calls `/instagram_status`, never calls `/instagram_test_publish`, never
validates Meta credentials, and never touches Telegram.

Milestone 11C (Deployment Readiness — Docker/config/S3-IAM verification,
including a correction to a stale S3 policy that was missing
`drafts/*`/`publications/*`/`executions/*`) is unchanged; see
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the full record. Milestone
11B (Controlled Instagram Live Validation — `/instagram_test_publish`,
one-time confirmation, dispatch-service reuse) is fully implemented and
still waiting on real credentials; see
[docs/INSTAGRAM_LIVE_VALIDATION.md](docs/INSTAGRAM_LIVE_VALIDATION.md).
Milestone 11A (Authoring Contract Consistency & Instagram Operational
Diagnostics) is unchanged. Milestones 1 through 10 are unchanged — see
their own sections below and [ROADMAP.md](ROADMAP.md) for what's next.

## Requirements

- Python 3.12+ (the Dockerfile uses `python:3.12-slim`; anything 3.10+ works
  locally)
- A Telegram bot token (from [@BotFather](https://t.me/BotFather)) — **not
  configured as part of this repository**; you'll need your own for local
  testing
- Your own Telegram numeric user ID, to allow-list yourself
- An AWS account with an S3 bucket, and an IAM user/role scoped to it (see
  "Required S3 setup" below) — **also not configured as part of this
  repository**

## Local setup

```bash
cd nrc-social-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Copy the environment template and fill in your own values:

```bash
cp .env.example .env
```

Required variables (the app fails fast at startup, with a clear error and no
stack trace, if any of these are missing or invalid):

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Your bot's token from BotFather. |
| `TELEGRAM_ALLOWED_USER_IDS` | Comma-separated Telegram numeric user IDs allowed to use the bot (e.g. `123456789,987654321`). |
| `AWS_ACCESS_KEY_ID` | Access key for the IAM identity described under "Required S3 setup". |
| `AWS_SECRET_ACCESS_KEY` | Secret key for that same IAM identity. |
| `AWS_REGION` | AWS region the bucket lives in (e.g. `us-east-1`). |
| `S3_BUCKET_NAME` | Name of the S3 bucket media is uploaded to. |
| `ANTHROPIC_API_KEY` | Your Anthropic API key. Never logged, never written anywhere but your local `.env`. |

Optional variables:

| Variable | Default | Description |
|---|---|---|
| `ENVIRONMENT` | `development` | Free-form label, currently only used in a startup log line. |
| `LOG_LEVEL` | `INFO` | Standard Python logging level name (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `S3_KEY_PREFIX` | `media` | Prefix under which all media objects are stored (see "S3 object layout" below). |
| `MAX_IMAGE_SIZE_MB` | `20` | Maximum accepted photo size. See "Accepted media types" for why 20 is the sensible default. |
| `MAX_VIDEO_SIZE_MB` | `20` | Maximum accepted video size. Same rationale as above. |
| `ANTHROPIC_MODEL` | `claude-opus-5` | Model used for media analysis. See "Model selection" below. |
| `ANTHROPIC_MAX_TOKENS` | `4096` | Max output tokens per analysis request (capped at 128,000). |
| `ANTHROPIC_REQUEST_TIMEOUT_SECONDS` | `60` | Per-request timeout, passed straight to the Anthropic SDK client. |
| `ANTHROPIC_MAX_RETRIES` | `2` | Retries for safe transient failures, handled entirely by the Anthropic SDK — see "Retry and timeout strategy" below. |
| `ANALYSIS_SCHEMA_VERSION` | `1` | Schema version stamped on every persisted analysis document. Only change this alongside a corresponding `AnalysisDocument.from_dict()` update. |
| `MAX_CLARIFICATION_QUESTIONS` | `4` | Safety cap on the adaptive clarification loop. See "Maximum-question policy" below. |
| `MAX_PLANNED_OUTPUTS` | `3` | Safety ceiling on outputs the Content Planning Engine may recommend. See "Maximum-planned-outputs policy" below. |
| `CONTENT_PLAN_SCHEMA_VERSION` | `1` | Schema version stamped on every persisted content-plan document. Only change alongside a corresponding `ContentPlanDocument.from_dict()` update. |

## Accepted media types

Only Telegram's two native media message types are accepted — never generic
"document" uploads, animations, or stickers:

| Telegram message type | Format | How it's identified |
|---|---|---|
| Photo | JPEG (`image/jpeg`) | Telegram always compresses "photo"-type messages to JPEG server-side; there's no other format this message type can carry. |
| Video | MP4 (`video/mp4`) | Telegram normalizes "video"-type messages to MP4 server-side; the message itself reports `mime_type`, which is checked directly. |

Filename extensions are never trusted or inspected — Telegram's own message
type (photo vs. video) and its own reported `mime_type` are the only
validation inputs, since neither can be spoofed by a client-supplied
filename. Anything else (documents, animations, audio, stickers) is
rejected with a short, generic message before any download is attempted.

Both `MAX_IMAGE_SIZE_MB` and `MAX_VIDEO_SIZE_MB` default to **20 MB** because
that's the standard Telegram Bot API's own ceiling for a file a bot can
download via `getFile` (using a self-hosted Bot API server can raise this,
but that's out of scope here) — raising either above 20 has no effect
against the public Bot API. Size is checked against Telegram's own reported
`file_size` *before* any download is attempted; if Telegram doesn't report a
size at all, the upload is rejected rather than downloaded unbounded.

## Required S3 setup (high level)

1. Create a dedicated S3 bucket (or a dedicated prefix in a shared bucket)
   for this bot. Keep **Block Public Access** enabled — the bot never makes
   objects public; the only externally-reachable URLs it ever produces are
   short-lived presigned GET URLs handed directly to Meta during Instagram
   dispatch (Milestone 10), never returned to Telegram or logged.
2. Create an IAM user or role for the bot with a policy scoped to
   `s3:PutObject` and `s3:GetObject` (which also covers the `HeadObject`
   existence checks `JsonObjectStore.exists()` performs) on every prefix
   this codebase actually writes to — **verified directly against every
   `_KEY_PREFIX`/`S3_KEY_PREFIX` constant in `src/`, not assumed**:

   | Prefix | Purpose | Since |
   |---|---|---|
   | `<S3_KEY_PREFIX>/*` (default `media/*`) | Uploaded photo/video originals — `PutObject` on upload, `GetObject` when the analysis pipeline reads media back, and again when Instagram dispatch generates a presigned URL for Meta | Milestone 2 |
   | `state/*` | Workflow records | Milestone 3 |
   | `conversation/*` | The active-workflow pointer per Telegram user | Milestone 3.5 |
   | `analysis/*` | Persisted analysis documents | Milestone 4A |
   | `plans/*` | Persisted content-plan documents | Milestone 5 |
   | `drafts/*` | Draft documents and immutable draft versions (two distinct uses share this one prefix — see `src/workflow/draft_store.py`) | Milestones 6, 7 |
   | `publications/*` | Immutable publication packages | Milestone 8 |
   | `executions/*` | Execution/dispatch records | Milestones 9, 10 |

   No `s3:DeleteObject` or bucket-level permission is needed anywhere —
   nothing in this codebase ever deletes an S3 object.
3. Put that identity's access key and secret in your local `.env` (never in
   a committed file) as `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`.

### S3 object layout

Each upload is stored at:

```
<S3_KEY_PREFIX>/<telegram-user-id>/<workflow-id>/original/<file-unique-id>.<ext>
```

`workflow-id` is generated fresh per upload and is also what's shown back to
the user as their upload's reference — and, since Milestone 3, it's also the
key under which that upload's workflow record is persisted (see "Workflow
persistence" below). This key layout is an internal storage detail — it's
never shown to the user as a literal path, and no presigned or public URL is
ever generated for an object.

## Workflow persistence

Immediately after a successful media upload, a **workflow record** is
created and durably persisted — this is what lets a conversation about that
media survive a restart, a deployment, or a crash, per
[docs/WORKFLOW.md](docs/WORKFLOW.md)'s reasoning for why state must be
persistent before Claude is introduced.

### S3 layout (frozen)

```
media/
    <telegram-user-id>/
        <workflow-id>/
            original/            <- Milestone 2, unchanged

state/
    <workflow-id>.json           <- Milestone 3: the workflow record

drafts/
    <workflow-id>.json           <- reserved; the store exists (DraftStore) but nothing writes here yet

analysis/
    <workflow-id>.json           <- Milestone 4A: the persisted Claude analysis document

conversation/
    <telegram-user-id>.json      <- Milestone 3.5: the active-workflow pointer, one per Telegram user

plans/
    <workflow-id>.json           <- Milestone 5: the persisted content plan document
```

`media/`, `state/`, `conversation/`, `analysis/`, and (since Milestone 5)
`plans/` contain data. `state/`, `drafts/`, `conversation/`, `analysis/`,
and `plans/` are fixed, non-configurable prefixes (unlike media's
`S3_KEY_PREFIX`) — this layout is frozen.

### Workflow document schema

```json
{
  "version": 3,
  "workflow_id": "…",
  "telegram_user_id": 123456789,
  "created_at": "2026-01-01T00:00:00+00:00",
  "updated_at": "2026-01-01T00:00:00+00:00",
  "state": "WAITING_FOR_USER",
  "media": {
    "telegram_file_id": "…",
    "telegram_file_unique_id": "…",
    "media_type": "photo",
    "mime_type": "image/jpeg",
    "file_name": null,
    "file_size": 123456,
    "s3_key": "media/123456789/<workflow-id>/original/<file-unique-id>.jpg",
    "uploaded_at": "2026-01-01T00:00:00+00:00"
  },
  "analysis": {
    "analysis_id": "…",
    "status": "COMPLETED",
    "schema_version": 1,
    "completed_at": "2026-01-01T00:00:05+00:00"
  },
  "draft": null,
  "conversation": [
    {
      "turn_id": "…", "role": "assistant", "type": "clarification_question",
      "content": "Is the main goal to build credibility for NRC, or drive discovery-call enquiries?",
      "question_id": "…", "target_field": "objective", "created_at": "2026-01-01T00:00:06+00:00"
    }
  ],
  "metadata": {},
  "clarification_context": {
    "version": 1,
    "content_type": {"value": "founder introduction", "source": "inference", "updated_at": "…"},
    "brand_name": null, "objective": null, "audience": null, "message_focus": null,
    "tone": null, "call_to_action": null, "platforms": null,
    "factual_context": {}, "user_preferences": {}, "unresolved": ["objective"]
  },
  "pending_question": {
    "question_id": "…",
    "text": "Is the main goal to build credibility for NRC, or drive discovery-call enquiries?",
    "purpose": "clarify primary objective",
    "target_field": "objective",
    "policy_version": 1
  },
  "content_plan": null
}
```

Once clarification finishes and content planning completes, `state`
becomes `GENERATING_CONTENT` and `content_plan` looks like:

```json
"content_plan": {
  "plan_id": "…",
  "status": "COMPLETED",
  "schema_version": 1,
  "output_count": 2,
  "primary_output_type": "instagram_reel_caption",
  "completed_at": "2026-01-01T00:00:10+00:00"
}
```

`analysis` is `null` until the analysis pipeline records an attempt (success
or failure) — see "Claude media analysis" below for the full shape and why
it's a reference, not the full analysis result. `clarification_context` and
`pending_question` (Milestone 4B) are `null` until the clarification
pipeline runs — see "Adaptive clarification conversation" below.
`content_plan` (Milestone 5) is `null` until the Content Planning Engine
records an attempt — see "Content Planning Engine" below.

The states persisted are the ones in [docs/WORKFLOW.md](docs/WORKFLOW.md)
that don't depend on preview/approval logic, including `GENERATING_CONTENT`
(introduced in Milestone 4B): `IDLE`, `RECEIVING_MEDIA`, `UPLOADING_MEDIA`,
`ANALYZING_MEDIA`, `WAITING_FOR_USER`, `GENERATING_CONTENT`, `COMPLETED`,
`FAILED`, `SAVED_AS_DRAFT`, `REJECTED`. A freshly created record starts in
`ANALYZING_MEDIA`, since per `docs/WORKFLOW.md`, that's `UPLOADING_MEDIA`'s
documented success exit — by the time a record exists, the upload has
already completed. Per Milestone 5's explicit design ("do not invent a new
workflow state"), a completed content plan leaves `state` unchanged at
`GENERATING_CONTENT` — the plan's own `status` field carries the real
outcome, exactly mirroring how `analysis`'s reference works within
`ANALYZING_MEDIA`.

**Milestone 4A left a documentation gap** (a successful single-pass
analysis had nowhere to go — `docs/WORKFLOW.md` only documents
`ANALYZING_MEDIA → WAITING_FOR_USER` and `ANALYZING_MEDIA →
GENERATING_CONTENT` as exits, and 4A implemented neither). **Milestone 4B
closes it exactly as `docs/WORKFLOW.md` already specifies**, with no
document changes needed: the clarification engine now performs the real
`ANALYZING_MEDIA → WAITING_FOR_USER` (question needed) or `ANALYZING_MEDIA
→ GENERATING_CONTENT` (sufficient context) transition, and a user's reply
re-enters `ANALYZING_MEDIA` per §3.4's documented re-entry condition before
the next decision. See "Workflow transitions" below.

### Versioning strategy

`version` is written on every document and checked on every read; a
document with an unsupported value raises `WorkflowVersionMismatchError`
rather than being guessed-at. Milestone 4B bumped `CURRENT_VERSION` to `2`
(adding `clarification_context`/`pending_question`); Milestone 5 bumped it
again to `3` (adding `content_plan`); Milestone 6 bumped it again to `4`
(adding `generated_draft`); Milestone 7 bumped it again to `5` (adding
`pending_edit`); Milestone 8 bumped it again to `6` (adding
`publication`) — versions `1` through `6` are all readable
(`SUPPORTED_VERSIONS = {1, 2, 3, 4, 5, 6}`); an older document simply reads
back with its missing fields as `null`. Existing documents are never
silently reinterpreted under a new shape.

### Concurrency strategy

Every workflow write is a single, atomic S3 `PutObject` of the *entire*
document — S3 never exposes a partially-written object to a reader, so a
torn write can never replace a valid document with a corrupt one. On top of
that, writes use **optimistic concurrency control** via S3's conditional-write
support:

- **Creating** a workflow uses `If-None-Match: *` — it fails if a document
  already exists at that ID (defense in depth; practically unreachable
  given random workflow IDs).
- **Updating** a workflow carries the ETag from the last load and writes
  with `If-Match: <that ETag>` — it fails if the document changed since,
  surfaced as `WorkflowConcurrentModificationError`.

There is no locking and no automatic retry-on-conflict — a conflict is
reported to the caller (`WorkflowConcurrentModificationError`) rather than
silently resolved, since nothing yet knows how to merge two concurrent
changes to the same workflow.

## Conversation resolution

Telegram messages don't naturally carry a workflow ID. A **conversation
record** — one per Telegram user, at `conversation/<telegram_user_id>.json`
— tracks which workflow is currently "active" for them, so `/status`,
`/cancel`, and any future incoming-message handler can find the right
workflow without the user supplying its ID.

### Conversation document schema

```json
{
  "version": 1,
  "telegram_user_id": 123456789,
  "active_workflow_id": "…",
  "state": "ACTIVE",
  "updated_at": "2026-01-01T00:00:00+00:00"
}
```

Deliberately minimal — nothing here duplicates data already in the
workflow document (no media info, no timestamps beyond its own
`updated_at`). `state` is `ACTIVE` (has a pointer) or `IDLE` (cleared or
never set) — a separate concept from `WorkflowState`.

### Repository design

Same three-layer pattern as workflow persistence: `ConversationStore`
(generic `JsonObjectStore` fixed to the `conversation/` prefix) →
`ConversationRepository` (dict ⟷ `ConversationDocument`, S3-error
translation) → `ConversationManager` (business logic: set/load/clear the
pointer, timestamps, concurrency). Application code never touches raw
JSON or S3 — it calls `ConversationManager`, or, to answer "which workflow
is this user talking about," `resolve_active_workflow()` in
`src/conversation/resolution.py`, which combines a `ConversationManager`
lookup with a `WorkflowManager` load into one `WorkflowDocument` result.

### Active workflow lifecycle

- **Set** — the moment a media upload succeeds (`src/media/ingestion.py`,
  right after the workflow record itself is created): points the user's
  conversation at the new `workflow_id`. If the user has never uploaded
  anything before, this creates their conversation record for the first
  time (a conditional create); otherwise it updates the existing one.
- **Load** — whenever `/status` or `/cancel` runs (and, in the future, any
  incoming reply): resolves the caller's active `workflow_id`, then loads
  that workflow's current document.
- **Clear** — only `/cancel` invokes this so far, right after rejecting the
  workflow. The capability supports clearing whenever a workflow reaches
  `COMPLETED`, `REJECTED`, `SAVED_AS_DRAFT`, or `FAILED`
  (`WorkflowState.TERMINAL_STATES`) — but nothing clears it *automatically*
  yet outside of `/cancel`'s own action. A workflow that reaches
  `COMPLETED` on its own (once Claude/approval exist) will stay "active" in
  the conversation record until a future milestone wires that up.

### Resolution algorithm

`resolve_active_workflow(telegram_user_id, conversation_manager,
workflow_manager)`:

1. Load the conversation record. No record at all, or a record with no
   active pointer → `NoActiveWorkflowError` ("no active post").
2. Load the workflow the pointer references. Missing →
   `StaleWorkflowPointerError` ("I couldn't find that post anymore").
3. Return the `WorkflowDocument` — **state-agnostic**: a resolved workflow
   may already be terminal (nothing auto-clears it), and resolution itself
   doesn't treat that as an error. `/cancel` is the one caller that checks
   for a terminal state itself, to report "nothing to cancel" instead of
   re-rejecting an already-finished post.

### Concurrency strategy

Identical to workflow persistence: every conversation write is one atomic
`PutObject` of the entire document, using the same `IfNoneMatch`
(create)/`IfMatch` (update) optimistic-concurrency pattern. Setting the
active workflow is an upsert (a conversation record is long-lived across a
user's whole history, unlike a workflow record, which is created once): it
loads first to decide create-vs-update, then writes conditionally either
way — a genuine conflict surfaces as
`ConversationConcurrentModificationError` rather than silently overwriting
someone else's more recent update.

## Claude media analysis

Immediately after a successful photo upload, `src/ai/analysis_service.py`
runs one single-pass, structured analysis of the image via the Claude API
and persists the result. This is intentionally the *entire* scope of
Milestone 4A — no follow-up questions, no multi-turn conversation, no
caption/title/hashtag/SEO/category generation, no preview, no approval.
Claude is a **consumer** of the existing workflow/conversation/media/storage
infrastructure here, not a redefinition of it — `src/ai/` only adds an
adapter, a persistence layer mirroring the existing pattern, and an
orchestrator; it never touches S3 or Telegram directly outside of the
existing `S3Storage`/`WorkflowManager`/`ConversationManager` it's handed.

### Model selection

Default: **`claude-opus-5`**, Anthropic's current flagship Opus-tier model
(confirmed via live Anthropic documentation at implementation time — Opus
4.8 has since been superseded). Opus was chosen over Sonnet 5 for analysis
quality on a task that feeds every later content-generation step; the
tradeoff is per-request cost/latency against Sonnet 5, which is cheaper and
faster but was judged less reliable for the "don't invent brand names,
identities, or claims" constraints this prompt depends on. Override via
`ANTHROPIC_MODEL` if that tradeoff should go the other way for your use
case (e.g. `claude-sonnet-5` for lower cost). Request-level `effort` is
fixed at `"medium"` (`src/ai/client.py`) — a middle ground for a bounded,
repetitive per-upload extraction task, not the model choice itself.
Adaptive thinking is left at the model's own default (on) rather than
explicitly configured, since Opus 5 enables it by default and disabling it
is subject to extra restrictions this workload doesn't need to opt into.

### Supported media types and known limitations

**Photo analysis only.** Video is explicitly out of scope for this
milestone: live Anthropic documentation (Messages API, vision guide, model
overview, migration guide) confirms the Claude API has no direct video
input support anywhere in the Messages API. Rather than build
frame-extraction infrastructure to work around that, a video upload is
accepted and stored exactly as before (Milestone 2 behavior unchanged) but
analysis is skipped with a clear, specific Telegram message
(`UnsupportedMediaTypeForAnalysisError` in `src/ai/errors.py`), and the
workflow is moved to `FAILED` (see "Failure-state behavior" below) so it
doesn't sit in a state implying analysis is still pending.

Images are also subject to a stricter size ceiling than Milestone 2's
Telegram-facing `MAX_IMAGE_SIZE_MB` (default 20 MB): Claude's own direct-API
image submission limit is 10 MB *base64-encoded*. `src/ai/analysis_service.py`
enforces its own `CLAUDE_MAX_IMAGE_RAW_BYTES` (7 MB raw, chosen to leave
headroom under the ~9.3 MB base64-encoded size that produces) — checked
once against the media record's reported `file_size` before any S3 read,
and again against the actual downloaded byte count, since a mismatched
reported size should never be trusted alone.

### Claude request architecture

`src/ai/client.py`'s `ClaudeClient` is the **only** module allowed to import
the `anthropic` SDK — application code (the orchestrator, handlers) never
touches it directly. It builds one `anthropic.Anthropic` client per process
(configured with `ANTHROPIC_REQUEST_TIMEOUT_SECONDS` / `ANTHROPIC_MAX_RETRIES`
at construction) and exposes a single method, `analyze_image(image_bytes,
mime_type)`, that base64-encodes the image, sends one `messages.create()`
call, and returns a minimal typed response (`text`, `input_tokens`,
`output_tokens`, `model`, `duration_seconds`) — **never** the raw SDK
response object, so no thinking-block content or full response can ever
leak into logs or persistence by accident.

### Structured output and prompt design

The response format is constrained server-side via `output_config: {format:
{type: "json_schema", schema: RESPONSE_SCHEMA}}` (`src/ai/prompts.py`) —
not free-form text — with `additionalProperties: false` and every field
`required`. `src/ai/parser.py` still parses and validates defensively on
top of that (defense in depth): valid JSON, correct shape, no missing or
extra fields, correct types per field — using `json.loads()`, never
`eval()`. A malformed or schema-mismatched response is a hard failure
(`MalformedAnalysisResponseError` / `AnalysisResponseSchemaMismatchError`)
— it is never partially accepted, silently repaired, or persisted as if it
were a valid result.

The system prompt (`SYSTEM_PROMPT` in `src/ai/prompts.py`) instructs Claude
to: describe only what's directly visible; clearly separate observation
from interpretation; never invent brand names, identities, locations, or
unsupported claims; never attempt to identify real people; not generate a
caption/title/hashtags/SEO keywords (out of scope for this step); keep
every field concise. The prompt and schema are versioned together as
`ANALYSIS_PROMPT_VERSION` (currently `1`) — bump it whenever a wording or
schema change could shift Claude's output shape. This is recorded on every
persisted analysis document, separately from `ANALYSIS_SCHEMA_VERSION` (the
*document's own* storage schema, `config.py`) — the two version numbers
change independently.

Response fields: `summary` (string) plus seven arrays of short phrases —
`visible_subjects`, `visual_style`, `dominant_themes`, `brand_signals`,
`content_opportunities`, `quality_observations`, `safety_notes`. See
`src/ai/models.py` for the exact `AnalysisResult` shape.

### Analysis persistence

Mirrors the existing Store → Repository → Manager pattern exactly:
`AnalysisStore` (a `JsonObjectStore` fixed to the `analysis/` prefix) →
`AnalysisRepository` (dict ⟷ `AnalysisDocument`, S3-error translation) →
`AnalysisManager` (business logic: idempotency check, conditional create,
OCC-protected supersede). Deliberately placed inside `src/ai/` rather than a
new top-level `src/analysis/` package, since the brief for this milestone
authorized `src/ai/` (plus `workflow/`, `storage/`, `utils/` as needed) as
the primary new-code location.

One analysis document per workflow, at `analysis/<workflow_id>.json`
(keyed by `workflow_id`, not a separate random ID):

```json
{
  "version": 1,
  "analysis_id": "…",
  "workflow_id": "…",
  "created_at": "2026-01-01T00:00:00+00:00",
  "updated_at": "2026-01-01T00:00:05+00:00",
  "status": "COMPLETED",
  "schema_version": 1,
  "prompt_version": 1,
  "model": "claude-opus-5",
  "media_type": "photo",
  "result": {
    "summary": "…",
    "visible_subjects": ["…"],
    "visual_style": ["…"],
    "dominant_themes": ["…"],
    "brand_signals": ["…"],
    "content_opportunities": ["…"],
    "quality_observations": ["…"],
    "safety_notes": ["…"]
  },
  "usage": { "input_tokens": 0, "output_tokens": 0 },
  "metadata": {}
}
```

`result` and `usage` are `null` on a `FAILED` record (`metadata` then holds
a short `failure_reason`, e.g. the exception class name — never a full
stack trace or raw API error body). The raw model response and any
chain-of-thought content are never persisted — only the validated
structured result and high-level token counts.

### Workflow-reference design

The workflow document gets a **lightweight reference**, attached via a
dedicated `WorkflowManager.attach_analysis_reference()` method (the same
load-then-conditional-save OCC pattern as `update_state()`) — never the
full analysis result, which stays solely in the `analysis/` document (single
source of truth, no duplication):

```json
{ "analysis_id": "…", "status": "COMPLETED", "schema_version": 1, "completed_at": "…" }
```

A successful analysis itself still leaves `state` unchanged at
`ANALYZING_MEDIA` — the real outcome lives in the `analysis` reference's
own `status` field. **This was flagged in Milestone 4A as a temporary gap
against `docs/WORKFLOW.md`'s two documented `ANALYZING_MEDIA` exits
(`WAITING_FOR_USER` / `GENERATING_CONTENT`), neither reachable by a
single-pass-only analysis step. Milestone 4B closes it**: immediately
after a successful analysis, the clarification engine (see "Adaptive
clarification conversation" below) makes the real decision and performs
whichever of those two exits actually applies — no further gap remains,
and `docs/WORKFLOW.md` required no changes.

### Retry and timeout strategy

Retries for safe transient failures (timeouts, connection errors, 429,
5xx) are handled **entirely by the Anthropic SDK's own `max_retries`/
`timeout` client options** (`ANTHROPIC_MAX_RETRIES` /
`ANTHROPIC_REQUEST_TIMEOUT_SECONDS`) — there is no additional
application-level retry loop layered on top, specifically to avoid
multiplying request counts (and cost) unexpectedly. If the SDK's own
retries are exhausted, the failure surfaces as a `Claude*Error`. **Since
Milestone 4B**, what happens next depends on whether that error is
retryable (`ClaudeTimeoutError` / `ClaudeRateLimitError` /
`ClaudeTransientError`, all now sharing a `ClaudeRetryableError` base) or
permanent (everything else) — see "Retryable versus permanent failures"
below; the short version is that a retryable failure no longer
automatically ends the workflow.

### Idempotency

Before ever calling Claude, `AnalysisService.analyze_workflow()` checks for
an existing analysis document for that `workflow_id`
(`AnalysisManager.find_existing`). If one exists with `status: COMPLETED`,
it's returned as-is and Claude is never called again — this is the primary
duplicate-call guard. `create_analysis()` also uses a conditional S3 create
(`IfNoneMatch: "*"`), so two concurrent first attempts can't both silently
"win" and overwrite each other. If a prior attempt exists but is `FAILED`,
a retry uses `supersede_failed_analysis()` instead — an explicit,
OCC-protected update (carries the ETag from the load) rather than a blind
overwrite, so two concurrent retries can't both silently win either.

### Failure-state behavior

Every expected failure (missing/unanalyzable workflow, unsupported media
type, media too large, S3 read failure, Claude auth/rate-limit/timeout/
5xx/refusal, malformed or schema-mismatched output, persistence failure) is
recorded as a `FAILED` analysis document (best-effort — a failure to even
persist the failure record is logged but never masks the original error).
What happens to the *workflow* then splits, per "Retryable versus
permanent failures" below: a **permanent** failure transitions
`ANALYZING_MEDIA -> FAILED` (an existing, documented state in
`docs/WORKFLOW.md` §7.3) and clears the active-workflow pointer via
`src/conversation/lifecycle.py`; a **retryable** failure leaves `state`
and the pointer untouched and instead records
`metadata["pending_retry"] = "analysis"`, so `/retry` can safely
re-attempt without abandoning the post. A failed analysis is never
reported to the user as successful, and the workflow is never left
silently stuck mid-analysis with no record of what happened.

### Logging

Logged: lifecycle milestones (analysis requested/started/completed,
Claude request started/completed, response validated, analysis persisted,
workflow updated), model identifier, token counts, request duration,
`workflow_id`, `telegram_user_id`, and exception *type* on failure. **Never
logged:** the Anthropic API key, raw media bytes or base64 payloads, full
prompts, full model responses or thinking-block content, AWS credentials,
S3 keys/bucket names to the user (only in structured logs), or any
Telegram-user-supplied sensitive content beyond what's already logged
elsewhere in this codebase.

### New dependency

`anthropic==0.120.0` — the official Anthropic Python SDK, pinned exactly
like every other dependency in `requirements.txt`. No other new dependency
was added: response validation uses only `json` (stdlib) and hand-written
type/shape checks, matching this codebase's existing pattern in
`workflow/models.py` and `conversation/models.py`.

## Adaptive clarification conversation

Immediately after a photo's analysis completes, `src/ai/clarification_service.py`
runs an adaptive, multi-turn conversation to decide whether enough is
understood to move on, or whether one more question is worth asking. This
is deliberately **not** a form or a fixed interview: there is no stored
list of questions anywhere in this codebase, and the engine can ask zero,
one, or several questions depending entirely on the specific upload, its
analysis, and how the conversation unfolds.

### How it actually avoids being a static questionnaire

This is worth being explicit about, since it's the milestone's central
requirement. Three separate things enforce it, not just prompt wording:

1. **No question list exists in code.** `src/ai/clarification_prompts.py`
   defines *categories* of information (`brand_name`, `content_type`,
   `objective`, `audience`, `message_focus`, `tone`, `call_to_action`,
   `platforms`, plus free-form `factual_context`) as a catalogue Claude may
   draw on — never an ordered sequence, never a template question per
   category, and nothing in `clarification_service.py` iterates over them.
2. **One unified decision, every turn, from scratch.** Every cycle — the
   first one right after analysis, and every one after a reply — sends
   Claude the media analysis summary, the current structured context, and
   recent conversation history, and asks it to decide fresh: is context
   sufficient, and if not, what single question would most reduce
   uncertainty right now. Nothing is precomputed or queued.
3. **Question-quality validation rejects generic-sounding questions before
   they're ever sent** — see "Question validation" below. A materially
   repeated question, one exposing an internal field name, or one asking
   about more than one thing at once is rejected and regenerated once, then
   fails safely rather than being sent.

### Model, prompt, and structured output

Same Claude client (`src/ai/client.py`) and model (`ANTHROPIC_MODEL`) as
media analysis, reusing the exact same `output_config.format:
{type: "json_schema"}` structured-output mechanism from Milestone 4A — no
new Claude integration pattern was introduced. One deliberate design
choice: **a single unified decision prompt**, not three separate ones for
"should I ask," "interpret the answer," and "what's next" — see
`clarification_prompts.py`'s module docstring for the full rationale
(fewer Claude calls, fewer places for the steps to disagree, no loss of
capability since `context_updates` naturally comes back empty on the very
first cycle and populated whenever there's a reply to interpret). The
prompt and response schema are versioned together as
`CLARIFICATION_POLICY_VERSION` (currently `1`), recorded on every
persisted `pending_question`.

**Bounded context construction**: only the most recent
`MAX_CONVERSATION_TURNS_IN_PROMPT` (6) conversation turns are sent
verbatim per call — the full accumulated understanding lives in the
structured context (always sent in full), so unbounded history growth
never inflates the request. The image itself is never re-sent after the
initial analysis; the persisted analysis *summary* carries what's needed.

### Structured context model

`src/ai/clarification_models.py`'s `ClarificationContext` is the
"evolving operational understanding" for one workflow — distinct from
`AnalysisResult` (what Claude directly observed in the media, immutable,
lives in `analysis/<workflow_id>.json`). It's stored inline on the
workflow document (`clarification_context`, see the schema above) rather
than as a separate S3 document — the smallest design that keeps it
cleanly separated from the analysis and the raw conversation turns without
adding a fourth persistence layer for what's fundamentally workflow-scoped
state.

Each named field is a `{value, source, updated_at}` triple, where `source`
is `analysis`, `inference`, or `user`. **Explicit user answers always win**:
a field already recorded with `source: "user"` is never silently
overwritten by an inference-sourced update (`ClarificationContext.apply_updates()`
enforces this directly, not just via prompt instruction) — but a *new*
user answer always overrides an older one, since that's a legitimate
correction, not a downgrade. Free-form `factual_context` /
`user_preferences` are plain string-keyed dicts without per-key source
tracking (a deliberate simplification — "where useful," not everywhere).

### Conversation-turn schema

Reuses the existing `WorkflowDocument.conversation` list (empty and
unused before this milestone). Two turn shapes, both minimal — no hidden
reasoning, no raw SDK response, no Telegram metadata beyond what's needed
to resume safely:

```json
{"turn_id": "…", "role": "assistant", "type": "clarification_question",
 "content": "…", "question_id": "…", "target_field": "objective", "created_at": "…"}

{"turn_id": "…", "role": "user", "type": "clarification_answer",
 "content": "…", "in_reply_to_question_id": "…", "created_at": "…",
 "telegram_update_id": 123456789}
```

`telegram_update_id` on the user turn is the duplicate-reply guard (see
"Idempotency and concurrency" below). Handlers and `ClarificationService`
never append to this list or touch `pending_question` directly — every
mutation goes through a dedicated `WorkflowManager` method
(`record_user_reply()`, `apply_clarification_decision()`).

### Answer interpretation

The unified decision prompt is instructed to extract everything useful
from a reply, not just the literal answer to the pending question — a
single natural-language reply commonly resolves several context fields at
once (e.g. "This is for NRC's Instagram and the goal is to introduce our
founder in a premium but approachable way" can resolve brand, platform,
objective, and tone together; see `test_ai_clarification_service.py`'s
`test_handle_user_reply_a_single_answer_can_resolve_multiple_context_fields`
for the exact scenario tested). No keyword matching, numbered options, or
rigid formatting is required or expected — free-form natural language is
the only input this engine understands. Corrections ("No, this isn't
behind the scenes — it's a campaign launch") are handled the same way: the
latest explicit statement is authoritative and overwrites the earlier one
per the context-precedence rule above; the original media analysis is
never argued with or silently reasserted. "Use your judgement" / "go
ahead" / "that's all" style replies are handled entirely through prompt
instruction (treat as a strong signal to stop asking optional questions),
not through hardcoded phrase matching in code — consistent with "no static
questionnaire" applying to the *code*, not just the question text.

### Question validation

Before a generated question is ever persisted or sent, `src/ai/question_validation.py`
enforces, in code (not just via prompt request): non-empty; at most
`MAX_QUESTION_LENGTH` (320) characters; at most one `?` (one primary
request); not a numbered list; none of a fixed set of internal/technical
terms (`json`, `schema`, `workflow`, `state:`, model/vendor names, etc.);
none of the internal snake_case category names; and not materially
identical (via `difflib.SequenceMatcher`, threshold 0.82) to a question
already asked this conversation. A failure triggers **exactly one**
controlled regeneration attempt (a fresh Claude call with the rejection
reason appended to the prompt) — never an unbounded corrective loop. If
the regenerated question also fails validation, the workflow fails safely
(`InvalidClarificationQuestionError`, permanent — see below) rather than
ever sending a broken or generic-sounding question.

### Workflow transitions

Exactly the two exits `docs/WORKFLOW.md` §3.4 already documents for
`ANALYZING_MEDIA`, and the one documented re-entry condition:

```
ANALYZING_MEDIA --(question needed)--> WAITING_FOR_USER
ANALYZING_MEDIA --(context sufficient)--> GENERATING_CONTENT
WAITING_FOR_USER --(user replies)--> ANALYZING_MEDIA   (re-evaluate)
```

No new workflow state was introduced. `GENERATING_CONTENT` is now
persisted (`src/workflow/states.py`) — this milestone stops there; nothing
acts on it yet (no content generation exists).

### Persist-before-send

`WorkflowManager.apply_clarification_decision()` performs **one atomic S3
write** — the updated `clarification_context`, the new assistant question
turn (if any), the new `pending_question`, the state transition, and any
metadata updates — all together, and `ClarificationService` returns to the
caller only after that write succeeds. `src/handlers.py` sends the
Telegram message only after the service call returns. A crash between
"decided to ask" and "the message was actually delivered" therefore always
resolves, on the next interaction, to a workflow that already has the
question durably persisted and resumable — never an unpersisted question
that could be lost.

### Plain-text reply routing

`src/handlers.py`'s `text_reply()` (replacing the old blanket fallback for
plain text) resolves the caller's active workflow via
`resolve_active_workflow()`, and only calls `ClarificationService.handle_user_reply()`
if that workflow is in `WAITING_FOR_USER` — for any other state (or no
active workflow at all), it replies with a short, safe message and never
touches Claude. Commands (`/start`, `/help`, `/status`, `/cancel`,
`/retry`) are routed through their own `CommandHandler`s and never reach
`text_reply()` at all (`filters.TEXT & ~filters.COMMAND`).

### Active-workflow lifecycle

The active pointer is kept while a workflow is `ANALYZING_MEDIA`,
`WAITING_FOR_USER`, or `GENERATING_CONTENT`, and cleared the moment it
reaches a terminal state (`COMPLETED`, `REJECTED`, `SAVED_AS_DRAFT`, or a
*permanent* `FAILED`) — never merely because analysis or a clarification
cycle completed. This is now wired through one reusable function,
`src/conversation/lifecycle.py`'s `clear_pointer_if_terminal()`, called by
every code path that can land a workflow in a terminal state (`/cancel`,
a permanent AI failure in either `analysis_service.py` or
`clarification_service.py`, the clarification-limit-exhausted case) —
rather than each of those independently re-deriving the terminal-state
check and its own error handling.

**New media while a workflow is already active** (any non-terminal state,
per `docs/WORKFLOW.md` §5 — not only `WAITING_FOR_USER`): the upload is
refused outright, before any S3 or Claude call, with the existing
workflow's reference and a prompt to `/cancel` it first. No second
concurrent workflow is ever created for the same Telegram user.

### Retryable versus permanent failures

Applies to both `analysis_service.py` (improved this milestone) and
`clarification_service.py` (new). `ClaudeTimeoutError`,
`ClaudeRateLimitError`, and `ClaudeTransientError` now share a
`ClaudeRetryableError` base — the SDK's own retries were already
exhausted, but the *workflow* is not treated as terminal: `state` and the
active pointer are left untouched, and `metadata["pending_retry"]` is set
to `"analysis"` or `"clarification_decision"`. Every other failure
(authentication, configuration, unsupported media, irrecoverably
malformed output, a validated-but-still-invalid question after
regeneration) is permanent: the workflow moves to `FAILED` and the pointer
is cleared via `clear_pointer_if_terminal()`.

### `/retry`

Evaluated per the brief's explicit "only add if it produces a clear and
bounded recovery path" test, and added: `retry()` in `handlers.py` reads
`metadata["pending_retry"]` off the caller's active workflow and
re-invokes exactly that one operation (`AnalysisService.analyze_workflow()`
then, on success, the first clarification cycle; or just
`ClarificationService.evaluate_and_advance()`) — never a different or
speculative operation, and never anything if no marker is set
(`"Nothing to retry right now."`). Retrying is safe against duplicate
Claude calls because both services are independently idempotent
(`analyze_workflow()` short-circuits on an existing `COMPLETED` analysis;
`evaluate_and_advance()` re-derives the decision purely from what's
already persisted, so a retry after a failed cycle is just running the
same, still-correct cycle again — nothing was partially applied by the
failed attempt, since persistence only happens after a full, valid
decision). For a reply-triggered cycle specifically, resending the same
answer works too (it's still the last turn in conversation history either
way) — `/retry` isn't the *only* recovery path, just the explicit one.

### Idempotency and concurrency

Continues the same optimistic-concurrency pattern as the rest of this
codebase (load-then-conditional-save, `WorkflowConcurrentModificationError`
on conflict, no locking, no automatic retry-on-conflict — a conflict is
reported to the caller). Duplicate-reply protection is two-layered: the
existing in-process `SeenUpdateTracker` (resets on restart) plus a
persistent check — `ClarificationService._is_duplicate_reply()` scans the
workflow's own conversation turns for a matching `telegram_update_id`
before ever appending a new one or calling Claude, which survives a
restart the in-process tracker wouldn't. **Known limitation**: this check
is a linear scan of the conversation list and only catches an *exact
repeat* of an already-recorded reply (the intended purpose) — it isn't a
distributed lock, and two genuinely concurrent requests for the same
workflow could theoretically both pass the check before either persists;
per this codebase's established stance (no database, no locking), such a
conflict is instead caught by the subsequent OCC-protected write.

### Maximum-question policy

`MAX_CLARIFICATION_QUESTIONS` (default **4** — see `.env.example`) is a
hard, server-side cap enforced in `clarification_service.py` regardless of
what Claude returns (the prompt also instructs Claude to respect it, but
the cap does not rely on that alone). At the limit, any `ASK_QUESTION`
decision is overridden to `CONTINUE`. If Claude still flags
`context_sufficient: false` at that point — genuinely essential context is
missing — the workflow fails safely (`ClarificationContextInsufficientError`,
permanent) rather than fabricating certainty or generating content from
inadequate grounding; the user is told concisely and can start over with
`/cancel` and a new, more detailed upload.

### Logging

Logged: clarification evaluation started/completed, whether a question
was required, question generated/persisted/sent, user answer
received/interpreted, context updated, next-question decision, whether
context is sufficient, workflow state transitions, retryable/permanent
failure, conversation conflicts, duplicate replies skipped — all as safe
identifiers and counts (`workflow_id`, `question_count`, decision type).
**Never logged**: full user answers, full question text, full prompts, raw
Claude output, any hidden reasoning, media payloads, API keys, or AWS
credentials.

### Known limitations

- No inline-button UI exists in this codebase yet, so `/retry` is a text
  command rather than the button `docs/WORKFLOW.md` §3.13 envisions for
  failure recovery generally — a deliberate, documented deviation for this
  milestone (see the completion report), not a silent contradiction.
- A retryable failure during the *very first* clarification cycle (right
  after analysis, before any question has ever been asked) has no
  automatic re-trigger beyond `/retry` — there's no polling or background
  retry; the user (or a future milestone) has to take an explicit action.
- The duplicate-reply guard is a linear scan of conversation history, not
  indexed — fine at this milestone's conversation lengths (bounded by
  `MAX_CLARIFICATION_QUESTIONS`), not designed for unbounded history.

## Content Planning Engine

Once the clarification conversation determines context is sufficient
(`GENERATING_CONTENT`), `src/ai/content_planning_service.py` automatically
runs a Content Planning Engine: it decides **what** content is worth
creating from this specific asset and **why**, and persists a validated
content plan. It never writes final captions, post bodies, hashtags, or
any other publishable copy — see "Strategy vs. writing" below for how
that's actually enforced, not just requested.

### Strategy vs. writing

The plan is deliberately shaped so nothing in it *can* be finished copy:

```json
// Not this:
{"instagram_caption": "At NRC, we believe..."}

// This:
{"output_type": "instagram_reel_caption", "purpose": "build founder credibility",
 "message_focus": "NRC connects branding, content and advertising into one system",
 "tone": ["confident", "premium", "human"],
 "cta_direction": "invite viewers to explore NRC without a hard sell"}
```

`content_plan_validation.py` backs this with code-level checks, not just
prompt wording: every strategic field is rejected if it exceeds
`MAX_STRATEGY_FIELD_LENGTH` (220 characters — generous for a direction,
short for real copy), contains a hashtag, contains a quoted line, or reads
as multi-sentence prose. This is heuristic, not perfect — see "Known
limitations" below.

### Dynamic output selection — not a static checklist

This is the milestone's central requirement, enforced three ways, not
just by asking nicely:

1. **No per-upload channel list exists in code.** `src/ai/content_plan_models.py`'s
   `OUTPUT_TYPE_REGISTRY` is a catalogue Claude selects from — never a
   sequence iterated over, never "always propose Instagram + LinkedIn +
   Threads." A single well-justified output is a complete, successful
   plan; nothing in the code path treats fewer outputs as incomplete.
2. **The prompt explicitly forbids defaulting to every channel** (see
   `CONTENT_PLANNING_SYSTEM_PROMPT`: "do not propose an output just
   because a channel exists," "a single strong recommendation is a
   complete, successful plan").
3. **Validation enforces distinctiveness and a hard ceiling.** Two outputs
   with an identical `message_focus` are rejected outright (no
   copy-paste-with-a-different-platform-name plans); the total output
   count is capped by `MAX_PLANNED_OUTPUTS` (server-side, regardless of
   what Claude returns).

### Supported output-type registry

A controlled set — Claude cannot invent a new output type; validation
rejects anything outside this list:

| Identifier | Display name | Channel |
|---|---|---|
| `instagram_reel_caption` | Instagram Reel caption | Instagram |
| `instagram_feed_caption` | Instagram feed caption | Instagram |
| `instagram_carousel_plan` | Instagram carousel plan | Instagram |
| `linkedin_post` | LinkedIn post | LinkedIn |
| `threads_post` | Threads post | Threads |
| `website_portfolio_entry` | Website portfolio entry | Website |
| `website_case_study_outline` | Website case-study outline | Website |

### Platform availability

Every registry entry carries `supports_planning` / `supports_generation` /
`supports_publishing` flags. Today, every entry is `planning: true,
generation: false, publishing: false` — this milestone only plans;
generation doesn't exist until a future milestone, and actual publishing
to any platform is explicitly deferred to [ROADMAP.md](ROADMAP.md) Phases
3–5. Recommending an output here is a strategic judgment about the asset,
never a claim that it can currently be generated or published — the
Telegram summary never implies otherwise (see "Telegram experience"
below).

**Flagged scope tension** (not silently resolved — see CLAUDE.md's
current-phase entry for the full note): `PRODUCT.md` / `ARCHITECTURE.md` /
`ROADMAP.md` describe Version 1's output as a single Telegram-previewed
post, with Instagram/LinkedIn/Threads as separate future-phase platform
adapters. This milestone's own brief explicitly asked for a *planning*
layer reasoning about channel-shaped output types now. Since nothing here
generates or publishes anything, and every planned output is shown only as
a short internal Telegram summary, this stays a strategy step, not a
platform-adapter implementation — no `docs/WORKFLOW.md` inconsistency
exists, so no planning document was edited. Flagged for a
[DECISIONS.md](DECISIONS.md) entry if a future milestone adds real
multi-channel generation.

### Content-plan schema

```json
{
  "version": 1,
  "plan_id": "…", "workflow_id": "…",
  "created_at": "…", "updated_at": "…",
  "status": "COMPLETED", "schema_version": 1, "prompt_version": 1, "model": "claude-opus-5",
  "strategy": {
    "content_type": "founder_introduction", "content_type_description": null,
    "primary_objective": "build founder credibility", "supporting_objective": null,
    "audience": ["growing business owners"],
    "central_message": "NRC connects branding, content and advertising into one system",
    "brand_positioning": "premium, connected", "tone_direction": ["confident", "premium"],
    "cta_direction": "invite viewers to explore NRC without a hard sell",
    "factual_constraints": ["brand: NRC"], "avoid": ["performance claims"]
  },
  "outputs": [
    {
      "output_id": "…", "output_type": "instagram_reel_caption", "priority": 1,
      "purpose": "build founder credibility", "audience": ["growing business owners"],
      "message_focus": "make the idea immediate and memorable",
      "tone": ["confident"], "cta_direction": "invite viewers to explore NRC without a hard sell",
      "required_context": [], "constraints": [], "generation_status": "NOT_STARTED"
    }
  ],
  "excluded_outputs": [{"output_type": "website_case_study_outline", "reason": "insufficient project detail"}],
  "usage": {"input_tokens": 0, "output_tokens": 0},
  "metadata": {}
}
```

`content_type` is a controlled classification (`work_showcase`,
`case_study`, `campaign`, `behind_the_scenes`, `founder_introduction`,
`brand_announcement`, `service_introduction`, `educational_content`,
`client_result`, `event`, `testimonial`, `general_brand_content`, or
`other` with a concise `content_type_description`) — never forced when
evidence is weak.

### Strategy hierarchy

**Primary objective** — the main outcome (credibility, awareness,
education, engagement, lead generation, portfolio proof, launch
awareness). **Supporting objective** — optional, only assigned when it
adds real value. **Central message** — the one idea kept consistent across
every output. **Output-specific message focus** — how each channel should
express that same strategy differently (e.g. Instagram: "make it
immediate and memorable"; LinkedIn: "explain the business reasoning") —
`content_plan_validation.py` rejects a plan where multiple outputs copy
the same `message_focus` verbatim.

### Priority

`priority: 1` is the single primary recommendation; `2`/`3` are supporting
or optional extensions. Validation requires **exactly one** priority-1
output and rejects any other priority value.

### Factual constraints and invented-claim prevention

The planner must preserve facts already established (brand name, campaign
name, founder role, required CTA, platform preference, etc.) and never
invent performance results, customer counts, awards, dates, locations, or
identities. `content_plan_validation.py` backs this with a heuristic
check: any specific number appearing in a strategic field that doesn't
appear anywhere in the context actually given to Claude is rejected as a
possible invented claim — see "Known limitations" for what this does and
doesn't catch.

### Brand context

Only context already available through the application's own runtime data
is used: persisted media analysis, persisted clarification context, and
the static product-level prompt guidance in
`content_plan_prompts.py`. No broader NRC brand-knowledge database exists
or is silently injected — per the brief's explicit instruction, that's a
deliberately deferred design question for a future milestone, not solved
here.

### Planning input construction

Bounded, same discipline as the clarification prompt: the persisted
analysis (summary, visible subjects, visual style, dominant themes, brand
signals, content opportunities) and the full structured clarification
context are sent in full (already condensed); only the most recent
`MAX_CONVERSATION_TURNS_IN_PROMPT` (8) conversation turns are sent
verbatim. The media itself is never re-sent — planning consumes the
persisted analysis, never a second visual-analysis request.

### Validation and one bounded regeneration attempt

Every plan is validated before persistence: schema version, supported
content type, supported output types only, output count in
`[1, MAX_PLANNED_OUTPUTS]`, no duplicate output types, exactly one
priority-1 output, valid priorities, meaningful (non-generic-length)
purpose/message_focus per output, no proposed/excluded output-type
conflict, no final-copy leakage, no known-generic strategic phrasing, and
no invented numeric claims. A failure triggers **exactly one** controlled
regeneration attempt (the rejection reason is appended to a fresh Claude
call) — never an unbounded loop. If the regenerated plan also fails, the
workflow fails permanently rather than persisting or sending anything
built on a rejected plan.

### S3 persistence and workflow reference

Mirrors `analysis/` exactly: `ContentPlanStore` → `ContentPlanRepository`
→ `ContentPlanManager`, one document per workflow at
`plans/<workflow_id>.json`, conditional S3 create for a fresh plan,
OCC-protected `supersede_failed_content_plan()` for retrying a prior
`FAILED` attempt. The workflow document gets only a lightweight reference
(`WorkflowManager.attach_content_plan_reference()`) — never the full
strategy or output list, which stays solely in `plans/`.

### Workflow state

Per the milestone's own explicit instruction, a completed plan leaves
`state` unchanged at `GENERATING_CONTENT` — no new workflow state was
invented; the plan's own `status` (and the workflow's `content_plan`
reference) carries the real outcome, exactly mirroring how Milestone 4A's
`analysis` reference works within `ANALYZING_MEDIA`. Planning is only ever
triggered when eligibility holds (`state == GENERATING_CONTENT` and a
completed analysis exists) — checked with **no side effects** on failure,
since an ineligibility signal means "this shouldn't have been called yet,"
not a planning failure.

### Idempotency and retryable/permanent failures

Identical pattern to analysis and clarification: `find_existing()` checks
for a `COMPLETED` plan before ever calling Claude — if found, it's reused
and Claude is never called again. A `ClaudeRetryableError` leaves `state`
and the active pointer untouched and sets
`metadata["pending_retry"] = "content_planning"`; every other failure
(auth/config errors, invalid stored analysis, irrecoverably malformed or
invalid plan) moves the workflow to `FAILED` and clears the pointer via
`src/conversation/lifecycle.py`. `/retry` gained a third pending-operation
value, `"content_planning"`, resolved and re-invoked the same way as
`"analysis"` / `"clarification_decision"`.

### Telegram experience

```
Perfect — I have enough context to prepare the content.
Planning the best content direction for this asset…

Content plan ready.

Recommended: Instagram Reel caption

Next, I'll prepare the first draft.
```

For a multi-output plan, recommendations are bulleted; a single-output
plan reads as a complete recommendation, not an incomplete list (see
`_format_plan_ready_message()` in `handlers.py`). Never shown: internal
priorities, schema fields, confidence, model name, token usage, or
excluded-output reasoning. New media is still refused outright while
`GENERATING_CONTENT` is active (the existing non-terminal-state check
already covers it — no separate logic was needed).

### Logging

Logged: planning started/context-loaded, Claude request
started/completed, plan validation succeeded/failed, plan persisted,
workflow reference attached, existing plan reused, retry
scheduled/completed, permanent failure — as safe identifiers and counts
(`workflow_id`, `plan_id`, output count, primary output type, model, token
usage, duration). **Never logged**: the full plan content, full user
answers, full prompts, raw Claude output, media content, API keys, or AWS
credentials.

### New dependency

None. Content planning reuses `anthropic` (already a dependency since
Milestone 4A) and the existing Claude client adapter; response validation
uses only `json` (stdlib) and hand-written checks, matching this
codebase's established pattern.

### Known limitations

- **Final-copy and generic-plan detection are heuristic, not semantic.**
  `content_plan_validation.py` catches hashtags, quoted lines,
  multi-sentence prose, length overruns, a small set of known-generic
  phrases, and a word-overlap "is this grounded in the provided context"
  check — not a full understanding of whether text constitutes finished
  copy or genuinely specific strategy. A sufficiently subtle violation
  could pass; a legitimately well-grounded but unusually-phrased strategy
  could occasionally be rejected (the groundedness check requires sharing
  at least one non-trivial word with the exact context provided, not a
  paraphrase-aware judgment).
- **Invented-number detection only catches numbers.** A fabricated
  qualitative claim (an invented partnership, an invented award name with
  no digits) isn't caught by this check — only specific numeric claims not
  present anywhere in the context given to Claude.
- **No distinct-variant mechanism for duplicate output types.** The brief
  allows "duplicate output types... if explicitly justified by distinct
  variants"; this milestone doesn't implement variant tagging, so any
  duplicate `output_type` in one plan is rejected outright, full stop.
- A retryable failure during content planning has no automatic
  re-trigger beyond `/retry` — same limitation as documented above for
  clarification.

## Primary Draft Generation Engine

Once `src/ai/content_planning_service.py` persists a `COMPLETED` content
plan, `src/ai/draft_generation_service.py` automatically generates the
plan's single priority-1 output as one reviewable draft. **Milestone 5
owns the strategy; Milestone 6 only writes it down.** Concretely, this
service never: adds a second output, removes a planned output, changes
priorities, changes the target platform/output type, introduces a
different CTA, reinterprets an explicit user correction, or generates more
than one variant of the selected output. It also never publishes or
approves anything — a validated draft only reaches `SHOWING_PREVIEW`, a
review state, never a terminal "done" state.

### Supported generation output types

Of the seven registered planning output types, exactly **one** —
`instagram_reel_caption` — has `supports_generation = True` as of this
milestone (`src/ai/content_plan_models.py`'s `OUTPUT_TYPE_REGISTRY`). This
was a deliberate choice, not a default: it's the consistently-used example
across both the planning and generation briefs, Telegram is Version 1's
only surface (so an Instagram-shaped caption is the most natural first
reviewable draft), and `PRODUCT.md`'s Version 1 vision is fundamentally
"one generated post," not a multi-platform batch. If a plan's priority-1
output is any other type, generation fails safely with a clear,
non-technical message — the plan itself stays intact and revisitable, it
is never silently regenerated as a different output type.

### Output-specific envelope and content model

The persisted draft is a shared envelope (`draft_id`, `workflow_id`,
`plan_id`, `output_id`, `output_type`, timestamps, `status`, schema/prompt
versions, `model`, `usage`, `metadata`) wrapping an output-type-specific
`content` shape — never one generic schema forced onto every format. Today
there's exactly one content model:

```json
{
  "version": 1,
  "draft_id": "…", "workflow_id": "…", "plan_id": "…", "output_id": "…",
  "output_type": "instagram_reel_caption",
  "created_at": "…", "updated_at": "…",
  "status": "READY_FOR_REVIEW", "schema_version": 1, "prompt_version": 1, "model": "claude-opus-5",
  "content": {
    "caption": "…",
    "hashtags": [],
    "cta": null
  },
  "source_versions": {
    "analysis_schema_version": 1, "content_plan_schema_version": 1,
    "draft_prompt_version": 1, "clarification_context_version": 1
  },
  "usage": {"input_tokens": 0, "output_tokens": 0},
  "metadata": {}
}
```

`hashtags` and `cta` are optional and commonly empty/`null` — neither is
added automatically (see "Hashtag and emoji policy" and "CTA handling"
below); their presence is a per-draft strategic choice, never a schema
requirement. `DraftStatus` is two-valued (`READY_FOR_REVIEW` / `FAILED`),
matching `AnalysisStatus`/`ContentPlanStatus`'s precedent — no
`GENERATING` transitional status is persisted, since the service either
completes a request or reports a controlled failure, never a
long-running in-between state worth its own record.

### Generation input construction

Bounded, same discipline as planning and clarification: the persisted
analysis, the full structured clarification context, the shared plan
`strategy`, and the one selected `output` are included in full — never the
excluded outputs or any other planned output, and never the raw media a
second time. Only the most recent `MAX_CONVERSATION_TURNS_IN_PROMPT` (6)
conversation turns are sent, each truncated to
`MAX_TURN_TEXT_CHARS_IN_PROMPT` (400) characters. Authority order, most to
least: the latest explicit user correction → earlier explicit user input →
the approved persisted clarification context → the content plan's
strategy/output → the media analysis → model inference — enforced by
prompt instruction (`_SHARED_RULES` in `draft_prompts.py`), since nothing
in this pipeline re-derives facts independently once they're established.

### Brand voice guidance

`draft_prompts.py`'s `BRAND_VOICE_PRINCIPLES` (confident, premium, clear,
human, specific, non-generic, restrained rather than exaggerated,
impactful without empty marketing language) is established **by this
milestone** — no prior project document defined NRC's voice adjectives
(verified via a full-repo grep before writing this). Treated as this
milestone's own deliberate baseline, not silently invented and not falsely
attributed to pre-existing doctrine — flagged here as a candidate for a
future [DECISIONS.md](DECISIONS.md) entry.

### No unsupported claims, and people/identity safety

The generation prompt explicitly forbids inventing performance figures,
growth percentages, client results, awards, counts, outcomes, dates,
locations, names, job titles, partnerships, testimonials, product
features, capabilities, rankings, market-leadership claims, or guarantees.
`draft_validation.py` backs this with the same two heuristics
`content_plan_validation.py` already uses: a word-overlap groundedness
check against the generation context, and a numeric-token check rejecting
any number in the draft that doesn't appear anywhere in that context — see
"Known limitations" for what these do and don't catch. A real person in
the media is never identified by name unless a name/role was given
explicitly; otherwise neutral terms (founder, team member, subject,
creative professional) are used.

### CTA handling

The draft must follow the plan's `cta_direction` exactly — never adding a
CTA the plan doesn't call for, and never falling back to a generic sales
phrase (`"DM us now"`, `"Book a call today"`, `"Click the link in bio"`,
`"Contact us"`, `"Follow for more"`) unless the plan and context actually
support it. `draft_validation.py` rejects any of those exact phrases in
the `cta` field, and separately rejects a `cta` duplicated verbatim inside
the `caption` (no reading the same CTA twice).

### Hashtag and emoji policy

Neither is automatic. Hashtags are optional and, when used, must be a
small, genuinely relevant set — `MAX_HASHTAGS` is a ceiling (default 5),
not a target; zero hashtags is a valid, often-preferable draft for a
restrained/premium brand. Emoji have no schema field and no code-enforced
requirement — the prompt allows them only where they genuinely suit the
tone, and `draft_validation.py`'s `MAX_EMOJI_COUNT` (3) is a ceiling
against visible excess, not a floor.

### Length and platform constraints

`MAX_INSTAGRAM_CAPTION_LENGTH` (default 2200) is Instagram's own real
caption ceiling — deliberately not Telegram's message length limit, which
is a delivery-mechanism detail, not a content constraint (see "Telegram
experience" below for how the *preview* separately respects Telegram's own
limit, defensively, without ever touching the persisted draft).

### Structured generation and defensive parsing

`ClaudeClient.generate_draft()` follows the exact pattern already
established by `plan_content()`/`decide_clarification()`: one structured-
output call via the shared `_send()` helper, at `DRAFT_GENERATION_EFFORT =
"medium"` — lower than content planning's `"high"`, because by the time
this call happens the hard strategic reasoning is already finished; this
call only has to execute one already-decided output faithfully.
`draft_parser.py` never uses `eval()` and never attempts to extract JSON
from a markdown-fenced or prose-wrapped response — a response that isn't
already bare, valid JSON is rejected as malformed outright, since silently
"fixing" a wrapped response is exactly the kind of leniency that could let
a hidden-reasoning preamble or a second variant slip through unnoticed.
Missing/extra/mistyped fields, an unregistered `output_type`, and an
unsupported document schema version are all rejected explicitly.

### Exactly one draft, never variants

The response schema has exactly three properties (`caption`, `hashtags`,
`cta`) with `additionalProperties: false` — there is no way to fit a
second caption, an `option_1`/`option_2` pair, or an "alternate hook" into
a schema-conformant response without it being rejected as an unrecognized
extra field. The generation prompt separately instructs "no variants, no
alternates, no option A / option B" as an explicit rule, not just a schema
side-effect.

### Validation and one bounded regeneration attempt

Every draft is validated before persistence (`draft_validation.py`):
length bounds, no markdown fence, no placeholder/template text, no
internal-terminology leakage (the copy itself may never mention "content
plan," "analysis," "clarification," "workflow," "output type," "schema,"
"confidence," "Claude," "Anthropic," "S3," or "model settings"), no known
generic agency phrasing, no self-explaining-its-own-strategy phrasing,
groundedness, no invented numbers, an emoji ceiling, hashtag count/format,
and CTA-genericness/duplication. A failure triggers **exactly one**
controlled regeneration attempt (the rejection reason appended to a fresh
Claude call) — never an unbounded loop; if the regenerated draft also
fails, the workflow fails permanently rather than persisting or previewing
anything built on a rejected draft.

### S3 persistence

Reuses the existing, previously-unused `src/workflow/draft_store.py`'s
`DraftStore` (reserved since Milestone 3, fixed to the `drafts/` prefix)
rather than a new store class: this milestone's composite key
`f"{workflow_id}/{output_id}"` passed through the store's existing generic
`_build_key()` already produces exactly `drafts/<workflow_id>/<output_id>.json`
— distinct from the reserved single-key `drafts/<workflow_id>.json` still
held for a future "Save Draft" user action (`WorkflowDocument.draft`,
docs/WORKFLOW.md §3.9), so the two can never collide despite sharing a
store class and prefix. `DraftRepository`/`DraftManager` live under
`src/ai/` (not `src/workflow/`), mirroring Milestone 4A's identical
placement call for `AnalysisRepository`/`AnalysisManager` — they operate on
the AI pipeline's `DraftDocument`, even though the underlying store class
lives in `src/workflow/`.

### Workflow reference and state transition

The workflow document gets only a lightweight `generated_draft` reference
(`draft_id`, `output_id`, `output_type`, `status`, `schema_version`,
`completed_at`) — never the full draft content, which stays solely in
`drafts/`. Deliberately named `generated_draft`, not `draft` verbatim as
one might expect — `WorkflowDocument.draft` already exists, reserved since
Milestone 3 for the unrelated future "Save Draft" action, so reusing that
name would have collided two different concepts under one field.
`WorkflowManager.attach_generated_draft_reference()` attaches the
reference **and** transitions `state` to `SHOWING_PREVIEW` in one atomic
write — combining both is what makes "persist the draft, then transition,
then preview" crash-safe rather than merely sequential (a crash between
"reference attached" and "state transitioned" simply cannot happen, since
there's only one write).

`SHOWING_PREVIEW` is not a new state: docs/WORKFLOW.md §3.7 already
documents `GENERATING_CONTENT -> SHOWING_PREVIEW` as this pipeline's real
exit — verified before writing any code, per this milestone's own explicit
instruction to check first. No documentation was changed.

### Idempotency and retryable/permanent failures

`DraftManager.find_existing(workflow_id, output_id)` is checked **before**
the workflow-state eligibility gate, unlike content planning's ordering —
because a completed draft is a valid idempotent outcome whether the
workflow is still sitting in `GENERATING_CONTENT` (a first attempt or
retry in flight) or has already reached `SHOWING_PREVIEW` (a duplicate
trigger after success); checking the draft store first lets both cases
short-circuit identically, with no second Claude call either way. A
`ClaudeRetryableError` leaves `state`/pointer untouched and sets
`metadata["pending_retry"] = "primary_draft_generation"`; every other
failure moves the workflow to `FAILED` and clears the pointer via
`src/conversation/lifecycle.py`. `/retry` gained a fourth pending-operation
value, `"primary_draft_generation"` — deliberately lowercase snake_case,
**not** the brief's own suggested `PRIMARY_DRAFT_GENERATION`, to stay
internally consistent with the existing three values (`"analysis"`,
`"clarification_decision"`, `"content_planning"`); this is a considered
naming deviation from the brief's literal casing, not an oversight.

### Telegram experience

```
Content plan ready.

Recommended: Instagram Reel caption

Next, I'll prepare the first draft.
Preparing the first draft for review…

Here's a draft ready for your review:

The founder's on-camera introduction carries premium credibility forward for NRC.

This is ready for your review — nothing has been published.
```

The preview is built by a dedicated formatter (`_format_draft_preview_message()`
in `handlers.py`), never by sending the persisted draft object directly —
it never exposes internal field names, `draft_id`/`output_id`, schema
versions, the model name, or token usage, and it never uses the word
"published." Formatting is plain text (this codebase never sets a Telegram
`parse_mode` anywhere, so there is no Markdown/HTML to escape or that could
render malformed); a defensive length truncation against Telegram's own
4096-character message ceiling exists but should be unreachable given
`MAX_INSTAGRAM_CAPTION_LENGTH`'s much smaller ceiling.

**If the preview send itself fails** (Telegram unavailable), the draft and
workflow state are left exactly as they are — no new Claude call, no
`FAILED` transition, just a logged failure. This is a deliberate,
documented refinement of docs/WORKFLOW.md §3.7's stated general preview-
failure behavior ("persistent failure moves to FAILED... since the user
has not yet seen anything to act on") for this specific milestone's
explicit instruction: once a draft is actually persisted and the workflow
has actually transitioned, the user's content already exists and a
Telegram hiccup alone shouldn't discard it. Not a structural
contradiction (no state names/transitions conflict) — noted here as a
deviation, not treated as a blocking inconsistency requiring a
docs/WORKFLOW.md edit.

`/status` now describes `SHOWING_PREVIEW` naturally ("Your draft is ready
for review"), and the existing non-terminal-state new-media guard already
covers it with no additional code — `SHOWING_PREVIEW` was simply added to
the same `WorkflowState` enum every other guard check already walks.

### Logging

Logged: generation started/context-loaded, Claude request
started/completed, draft validation succeeded/failed (with one
regeneration), draft persisted, workflow reference attached, plan output
marked generated, existing draft reused, retry scheduled/completed,
permanent failure, preview-send failure — as safe identifiers and counts
(`workflow_id`, `draft_id`, `output_id`, `output_type`, model, token usage,
duration). **Never logged**: the full draft body/caption, full prompts,
raw Claude responses, user answers, media content, API keys, or AWS
credentials.

### New dependency

None. Draft generation reuses `anthropic` (already a dependency) and the
existing Claude client adapter; parsing/validation use only `json`
(stdlib), `re` (stdlib), and hand-written checks — matching every other
pipeline stage in this codebase.

### Known limitations

- **Generic-draft and invented-claim detection are heuristic, not
  semantic** — the exact same word-overlap groundedness check and
  numeric-token invented-claim check already documented as limitations
  for `content_plan_validation.py` apply here for the same reasons. A
  sufficiently subtle violation could pass; an unusually-phrased but
  genuinely grounded draft could occasionally be rejected.
- **Internal-terminology leakage detection is a fixed substring
  denylist** — a paraphrase of a banned term (e.g. describing "the plan"
  without the word "plan") would not be caught.
- **Only one output type currently supports generation.** A plan whose
  priority-1 output is any other registered type fails generation safely
  (`UnsupportedGenerationOutputTypeError`) rather than falling back to a
  different format — the plan itself remains intact for when that format
  gains support.
- A retryable failure during draft generation has no automatic re-trigger
  beyond `/retry` — same limitation as documented above for planning and
  clarification.

## Telegram Draft Review, Editing and Approval Workflow

Once Milestone 6 shows a draft, the user acts on it via four inline
buttons — **Approve**, **Edit**, **Save Draft**, **Reject** — implemented
by `src/handlers.py`'s `review_action()` (a `CallbackQueryHandler`) and a
new immutable draft-versioning layer (`src/ai/draft_version_models.py`,
`draft_version_repository.py`, `draft_version_manager.py`). **Milestone 5
still owns the strategy and Milestone 6 still owns the first draft — this
milestone only lets the user review, revise, save, approve, or reject
it.** Approval never publishes anything (see "Approval" below).

### Existing workflow states verified first

Per this milestone's own explicit instruction, the state machine was
checked against docs/WORKFLOW.md and `src/workflow/states.py` *before*
writing any code:

- **`EDITING`** — docs/WORKFLOW.md §3.8 already names and fully specifies
  it ("Capture what the user wants changed... Exit conditions: user
  specifies the field(s)... → `GENERATING_CONTENT`"). Added to the
  persisted enum; no new semantics invented.
- **Edit generation** reuses the *existing* `GENERATING_CONTENT` state,
  exactly as docs/WORKFLOW.md §3.6 already documents ("Or: user submitted
  an edit from `EDITING` (partial pass)") — the same "one state serves
  multiple pipeline stages" precedent Milestones 5/6 already established.
- **`APPROVED`** is documented (§3.10) but **deliberately not added** to
  the persisted enum. Docs/WORKFLOW.md itself describes it as strictly
  transitional ("Allowed user actions: None — a brief, system-driven
  transition") whose only job — a "finalization write" — has no distinct
  content in this system beyond persisting approval metadata, a write
  this codebase already performs atomically with every state transition.
  A separately-observable `APPROVED` snapshot would be a state no code
  path could ever meaningfully occupy, so Approve transitions directly
  `SHOWING_PREVIEW` → `COMPLETED` in one atomic write (see
  `WorkflowManager.approve_draft()`). (The milestone brief's own
  illustrative state diagram even mis-names `EDITING` as
  `"EDITING_CONTENT"` — confirming that sketch is shorthand, not a
  literal source of truth; docs/WORKFLOW.md itself was treated as
  authoritative.) `APPROVED` remains available for a future milestone
  with genuine multi-step finalization/publishing, where the distinction
  would become real.
- No documentation was found to be inconsistent, so no planning doc was
  edited.

### Immutable draft versioning and the current-version pointer

Reuses the existing, previously-established `drafts/` prefix a *third*
distinct way:

```
drafts/<workflow_id>/<output_id>.json                     Milestone 6's flat draft (frozen historical relic post-migration)
drafts/<workflow_id>/<output_id>/versions/<n>.json         immutable, never overwritten
drafts/<workflow_id>/<output_id>/current.json              the mutable pointer — the only thing that changes
```

`DraftDocument` (`draft_models.py`) is reused as the version envelope
rather than duplicated — document schema bumped 1 → 2 to add
`version_number`, `parent_version_number`, and `edit_instruction_reference`
(a version-1 document defaults these to `version_number=1`,
`parent_version_number=None` — exactly what a true first version should
have). The current-version pointer is a small new model
(`CurrentDraftPointer`, `draft_version_models.py`) carrying only
`current_draft_id`/`current_version_number`/`status`
(`ReviewStatus`: `READY_FOR_REVIEW` / `APPROVED` / `SAVED_AS_DRAFT` /
`REJECTED`) — deliberately **not** on the immutable version itself, since
review lifecycle status belongs to the *review*, not the *content*.

### Migration from Milestone 6's flat draft layout

**Option A** (of the two the brief allows): the first time any review
action needs the current version and no `current.json` exists yet,
`resolve_current_draft()` (`draft_version_manager.py`) loads the existing
Milestone-6 flat draft, copies it into an immutable version 1, and creates
`current.json` pointing at it — idempotent and race-safe (both new writes
are conditional creates; a lost race just means re-reading what the
winner already wrote). The original flat object is **never modified or
deleted** — it becomes an inert historical duplicate. This is the single
shared entry point used identically by `draft_editing_service.py`,
`review_action()`, and `/status`, so the migration trigger lives in
exactly one place.

### Version-number allocation and OCC

`create_next_version()` reads the current pointer's version number and
ETag *before* the Claude call (so the OCC check spans the whole edit
transaction, not just the final write), writes the candidate next version
as a brand-new immutable object (conditional create), then advances the
pointer with `IfMatch=<that ETag>`. If the pointer write loses a race, the
just-written version becomes an **orphan**: valid, immutable, and
permanently unreferenced — deliberately left in place (no S3 deletion, no
renumbering) since version-resolution only ever reads through
`current.json`, never scans the `versions/` namespace. The conflict
propagates to the caller as `DraftVersionConflictError`, treated as
retryable (`pending_retry = "draft_editing"`) — a fresh attempt re-reads
the pointer and starts its own idempotency check over.

### Natural-language editing

The user selects **Edit**, the bot asks "Tell me what you would like
changed," and the *next* authorized text message becomes the instruction
— never a new clarification question, never a media-analysis or
content-planning rerun. `draft_edit_prompts.py`'s system prompt declares
the output type/platform authoritative and instructs Claude to follow the
latest instruction while preserving everything the instruction doesn't
ask to change (targeted edits shouldn't trigger unrelated rewrites).
`draft_edit_validation.py` adds a small set of **deterministic,
high-confidence** instruction-alignment checks on top of the shared
content-quality rules (remove hashtags → empty, remove CTA → none, remove
emoji → zero, shorten → meaningfully shorter, preserve-a-quoted-line →
present verbatim) — deliberately not a general rule engine; open-ended
instructions get prompt guidance and the shared checks only.

An explicit **pre-flight** scan (`detect_unsupported_platform_switch()`)
rejects an out-of-scope request ("Turn this into a LinkedIn post") before
any Claude call — the current version is left completely untouched and
the workflow returns straight to `SHOWING_PREVIEW`.

### Persistence ordering and failure taxonomy

Instruction persisted (as a conversation turn, with a `turn_id` doubling
as the operation ID) → `EDITING` → `GENERATING_CONTENT` **before** any
Claude call — required for `/retry` to resume safely after a crash.
Revision generated → parsed → validated → persisted as a new immutable
version → pointer advanced → workflow reference updated → transitioned
back to `SHOWING_PREVIEW` → preview sent. A deliberate, documented
refinement of docs/WORKFLOW.md §3.6's blanket "generation fails → FAILED"
rule, since editing (unlike a first-time generation) always has a
perfectly good existing draft to fall back to:

- **Retryable** (Claude timeout/rate-limit/transient, or a version-pointer
  race): current draft, `pending_edit`, and state all untouched;
  `pending_retry = "draft_editing"`.
- **Non-corrupting permanent** (out-of-scope instruction, malformed/
  mismatched response, a validation failure surviving one bounded
  corrective attempt, a detected stale parent version): current draft
  unchanged, no new version, back to `SHOWING_PREVIEW` — never `FAILED`.
- **Corrupting permanent** (missing content plan, no current draft to
  edit, auth/config errors): `FAILED`, pointer cleared — same as every
  other pipeline stage.

### Idempotency and duplicate-update protection

No separate persisted "processed update IDs" store — the **workflow state
itself** is the durable idempotency guard, extending the same pattern
already used for clarification answers:

- A duplicate **edit-instruction** text update: once
  `record_edit_instruction()` transitions `EDITING → GENERATING_CONTENT`,
  a second delivery of the same update finds state ≠ `EDITING` and is
  routed to the generic "not waiting" response — never re-processed.
- A duplicate **Edit-button** callback: `review_action()` detects the
  workflow is already `EDITING` with the same `expected_parent_version`
  and just re-sends the prompt, without starting a second operation.
- A duplicate **Approve/Save/Reject** callback: idempotent-repeat
  detection (`_is_repeat_of_completed_action()`) recognizes the workflow
  is already in the exact target state/status *for the exact version
  named*, and returns the same success message again — no side effects.
- `pending_edit.status` staying `"COMPLETED"` (not cleared to `None`)
  after a successful edit lets a late-arriving duplicate of the
  completion-triggering update short-circuit with zero Claude calls.

### Stale-action handling

Every review button embeds `(workflow_id, version_number)` in its
callback data (`review:<action>:<workflow_id>:<version_number>` —
`workflow_id` used as-is, not shortened, since this app already exposes
it verbatim as "Reference: ..." in every other message). The
server-side current pointer is always the authority: if the callback's
version doesn't match, the action is rejected with zero Claude calls and
zero S3 writes, the user is told a newer version is available, and the
current preview is resent. Old buttons are never disabled or edited —
they simply become harmless once the version they name is no longer
current.

### Approval, Save Draft, and Reject semantics

All three apply only to the authoritative current version (stale/repeat
handling above), update only the current-version pointer's `status` (never
the immutable version), and update the workflow's lightweight
`generated_draft` reference — never duplicating draft content into the
workflow document. **None of the three publishes anything** —
`APPROVE_SUCCESS_MESSAGE` explicitly says "ready for the publishing
step," never "published"; Save Draft and Reject explicitly say "Nothing
has been published." All persisted draft versions, the content plan, and
the analysis remain exactly as they are regardless of outcome — Reject
never deletes an S3 object. Each terminal transition
(`COMPLETED`/`SAVED_AS_DRAFT`/`REJECTED`) calls the same
`clear_pointer_if_terminal()` helper every other terminal transition in
this codebase already uses.

### `/retry` and `/status`

`/retry` gained a fifth pending-operation value, `"draft_editing"` —
lowercase snake_case, consistent with the existing four. `/status` now
names the current version while `SHOWING_PREVIEW` ("Current version: N"),
describes `EDITING` ("I'm waiting for your edit instructions for version
N"), and distinguishes a pending edit retry from ordinary
`GENERATING_CONTENT` (both states are shared with earlier pipeline
stages, exactly per precedent).

### Logging

Logged: review action received, edit mode entered, instruction persisted,
current draft resolved, stale action rejected, duplicate reused, Claude
edit request started/completed, validation succeeded/failed, immutable
version persisted, pointer advanced, workflow reference updated, revised
preview sent, approval/save/reject persisted, retry scheduled/completed,
concurrency conflict — safe identifiers only (`workflow_id`, `draft_id`,
`output_id`, version numbers, model, token usage, duration). **Never
logged**: the full draft body, the full edit instruction, full prompts,
raw Claude responses, API keys, or AWS credentials.

### New dependency

None. Reuses `anthropic`, python-telegram-bot's existing
`InlineKeyboardButton`/`InlineKeyboardMarkup`/`CallbackQueryHandler`
(already a dependency, not previously used), and this codebase's own
`json`/`re`/hand-written validation pattern.

### Known limitations

- The same heuristic-groundedness/invented-number/internal-terminology
  limitations already documented for `draft_validation.py` apply
  identically to edits (`validate_draft_edit()` reuses those checks
  unchanged).
- Deterministic instruction-alignment checks only cover a handful of
  clear, high-confidence categories (remove hashtags/CTA/emoji, shorten,
  preserve-a-quoted-line) — an open-ended instruction outside these gets
  prompt guidance only, not a code-level correctness check.
- The platform-switch pre-flight scan is a keyword/phrase heuristic
  (`detect_unsupported_platform_switch()`), not a semantic understanding
  of intent — a sufficiently indirect phrasing could evade it (the
  system prompt is the second line of defense in that case).
- A retryable failure during editing has no automatic re-trigger beyond
  `/retry` — same limitation as every earlier pipeline stage.

## Publication Preparation Layer

Once a draft is approved (Milestone 7), Milestone 8 converts *that exact
approved version* into a small, immutable, platform-facing publication
package — and does nothing else. It never rewrites content, never calls
Claude, and never publishes anything. `src/publication/` (`models.py`,
`errors.py`, `store.py`, `repository.py`, `manager.py`, `builder.py`,
`validation.py`, `service.py`) is a new, independent domain, mirroring the
Store → Repository → Manager → Service layering every other domain in
this codebase already uses.

### Approval semantics verified first

Per this milestone's own explicit instruction, approval's current
behaviour was checked before any code was written: `approve_draft()`
(`src/workflow/manager.py`) performs one atomic `SHOWING_PREVIEW` →
`COMPLETED` write; approval metadata (`approved_at`,
`approved_by_telegram_user_id`) lives in both the current-draft-version
pointer and the workflow's lightweight `generated_draft` reference; and
`COMPLETED` is a `TERMINAL_STATES` member, so the caller's active-workflow
pointer would normally be cleared immediately (Milestone 7's
`clear_pointer_if_terminal()`). That last fact directly shaped this
milestone's biggest design decision — see "Pointer-clearing" below. No
inconsistency was found in docs/WORKFLOW.md, and `COMPLETED` was not
repurposed; a new workflow-document field (`publication`, schema v5 → v6)
and a new publication-domain status enum were added instead, exactly as
the brief's own guidance preferred ("prefer publication status inside the
publication domain over adding workflow states").

### One package, from the exact approved version

A publication package represents exactly one approved workflow + one
approved draft + one draft version + one output + one target channel —
never multiple outputs, never a later or earlier version than the one
actually approved. `PublicationPreparationService.prepare_publication()`:

1. Loads the workflow; requires `state == COMPLETED` and
   `generated_draft.status == "APPROVED"` (else
   `WorkflowNotEligibleForPublicationError`, no side effects).
2. Checks `PublicationManager.find_existing()` first — idempotency before
   anything else, matching every other service in this codebase.
3. Resolves the deterministic channel from the approved output's type
   (`resolve_channel()`, never Claude, never content inference).
4. Resolves the authoritative current draft version
   (`resolve_current_draft()`, the same Milestone-7 helper
   `draft_editing_service.py` and `review_action()` already use) and
   verifies it still matches the workflow's own approval metadata
   (`ApprovedVersionLinkageMismatchError` if not — a data-consistency
   signal, should be unreachable since nothing can edit a draft again
   after approval).
5. Loads the completed content plan and confirms the approved output_id
   still exists in it.
6. Builds media references and content (`builder.py` — see "Content
   mapping" below), then validates the whole package
   (`validation.py`).
7. Persists a `READY_FOR_PUBLISHING` package (`PublicationManager`),
   attaches a lightweight reference back onto the workflow, and clears
   the active pointer.

### Channel resolution: a separate, narrower mapping

`src/publication/models.py`'s `_CHANNEL_BY_OUTPUT_TYPE` is a **new,
separate** mapping from `content_plan_models.OUTPUT_TYPE_REGISTRY` —
deliberately not derived from that registry's `supports_generation` flag,
even though today both would produce the same single entry
(`instagram_reel_caption` → `instagram`). "Registered for planning",
"supports generation", and "supports publication preparation" are three
independently-narrowing gates; a future milestone enabling generation for
a second output type must not automatically make it publication-ready.
`resolve_channel()` raises `UnsupportedPublicationOutputTypeError` for
anything not explicitly listed — never a silent fallback to Instagram.

### Content mapping: mechanical only

`build_publication_content()` performs **mechanical transformations
only** — trimming accidental outer whitespace and stripping a redundant
leading `#` from hashtags — never a semantic rewrite, never brand-voice
reapplication. The caption is assembled by the approved draft's own
fields as-is (`caption`, `hashtags`, `cta`); Milestone 6 draft generation
already ensures the CTA is a separate field, not duplicated inside the
caption text, and `validation.py` defensively re-checks this. Any future
platform-specific formatting belongs in a publisher adapter or a
deliberately versioned channel formatter — not an AI rewrite.

### Media: durable references only

`build_media_references()` maps the workflow's single media record
(V1 ingestion never stores more than one) into exactly one
`MediaAssetReference`, holding only a durable `{"bucket": ..., "key": ...}`
S3 reference — never bytes, never a presigned or temporary Telegram URL,
never credentials. A future publisher requests temporary access through
its own infrastructure boundary; this package only ever holds identifiers.

Discovered while wiring this up: `src/ai/analysis_service.py` only
analyzes `image/jpeg` uploads today (`_ANALYZABLE_MEDIA_TYPES`) — video is
rejected before it can ever reach an approved draft, a genuine
**pre-existing** scope tension (the output type's name implies a video
"Reel"), not something this milestone introduces. Rather than
artificially restricting the publication-domain's own media-type
allowlist to match today's reachable path, `video/mp4` is allowed
alongside `image/jpeg` ahead of that gate ever lifting, so this mapping
won't need to change the day it does.

### Persistence: a new, dedicated namespace

```
publications/<workflow_id>/<output_id>.json
```

Keyed by `(workflow_id, output_id)` — the same composite-key precedent
`DraftManager` (Milestone 6) already established — giving a deterministic,
restart-safe lookup "for free," with `publication_id` itself just a
random `pub_<uuid4().hex>` like every other domain's internal ID.
`create_publication()` is a conditional create (never overwrites a
`READY_FOR_PUBLISHING` package); `supersede_failed_publication()` is the
one place an existing (`FAILED`) record is replaced, using its loaded
ETag for optimistic concurrency.

### Pointer-clearing: deliberately deferred into this service

Milestone 7's `clear_pointer_if_terminal()` convention clears a user's
active-workflow pointer the instant a workflow reaches a terminal state —
which `COMPLETED` already is. But publication-preparation crash-recovery
and `/retry` need to still find the workflow *afterward*. Rather than
inventing a new lookup mechanism (searching S3, a second persisted
"publication-intent" record, etc.), `_handle_approve_action()` no longer
clears the pointer immediately after `approve_draft()` succeeds — that
responsibility moved into `PublicationPreparationService` itself, which:

- **Clears the pointer on success** (fully resolved — nothing left to
  do).
- **Clears the pointer on a permanent preparation failure** — nothing is
  left to retry for *publication*, but (a deliberate, narrow exception to
  every other "permanent failure" in this codebase) the underlying
  *authoring* workflow is not broken; a lightweight `publication`
  reference with `status: "FAILED"` is attached first so `/status` can
  still describe "permanently unavailable" rather than "no active post."
  The user's manual escape hatch (`/cancel`) already clears the pointer
  for any terminal-state workflow without re-rejecting the approved
  draft, so nothing is stuck forever.
- **Leaves the pointer untouched on a retryable failure** — so `/retry`
  can find the same workflow through the caller's ordinary active-pointer
  resolution, with no new infrastructure required.

This also resolves the "Completed Workflow Lookup" concern the brief
anticipated might need a new lookup mechanism: because the pointer stays
active through the whole publication-preparation lifecycle (until success
or permanent failure), `/retry`'s and `/status`'s existing
`resolve_active_workflow()` call continues to work completely unchanged.

### Failure taxonomy (no Claude calls, so a different split)

Since this service makes zero Claude calls, the retryable/permanent split
is not Claude-error-based like every earlier pipeline stage. Instead:
`PublicationPermanentError` subclasses are the enumerated, specific
business-logic conditions (unsupported output/media type, missing/
inconsistent approval metadata, missing content plan, failed validation)
— never retried automatically, a best-effort `FAILED` package persisted
for audit. Everything else under the generic `PublicationError` base
(persistence errors, concurrent-modification conflicts, an unexpected
workflow-load failure) is treated as retryable by default, since every
known permanent condition already has its own specific type above —
`metadata["pending_retry"] = "publication_preparation"` is set, and the
pointer is left untouched.

### `/retry` and `/status`

`/retry` gained a sixth pending-operation value, `"publication_preparation"`
— lowercase snake_case, consistent with the existing five — resolved via
the same shared `_run_publication_preparation_and_reply()` helper the
approve handler uses, and never reapproves the draft or asks the user to
review it again. `/status` now distinguishes three outcomes for a
`COMPLETED` workflow: prepared and ready, retry pending, or permanently
unavailable — using only the workflow's own lightweight `publication`
reference and `pending_retry` marker, never a raw internal state name,
and never the word "published."

### Approve/retry Telegram responses

- Success: *"Approved.\n\nYour content has been prepared for publishing.
  Nothing has been published yet."*
- Retryable failure: *"Approved.\n\nThe draft is safe, but I could not
  prepare it for publishing right now. You can retry this step."*
  (never "approval failed" — approval is never in question)
- Permanent failure: *"The draft is approved, but this output is not yet
  supported for publishing preparation."*

### Logging

Logged: preparation requested, existing package reused, channel resolved,
current draft resolved, package persisted, permanent/retryable failure
classified, publication reference attached, pointer cleared/retained —
safe identifiers only (`workflow_id`, `output_id`, `publication_id`,
`draft_id`, version numbers, channel, status). **Never logged**: the
caption, hashtags, CTA, full workflow document, raw Claude response
(there isn't one), API keys, or AWS credentials.

### New dependency

None. No social-platform SDK, no OAuth library — this milestone never
connects to Instagram or any external platform.

### Known limitations

- V1 publication packages support exactly one channel/output type
  (`channel=instagram`, `output_type=instagram_reel_caption`) — matching
  Milestone 6's only generation-enabled output type. "Registered for
  planning" does not mean "generated"; "generated" does not necessarily
  mean "publishable" — each is its own explicit gate.
- A retryable failure during preparation has no automatic re-trigger
  beyond `/retry` — same limitation as every earlier pipeline stage.
- "Publication Package" (this milestone, immutable) and "Publication
  Execution" (a future, mutable platform-attempt record) are
  deliberately kept as separate concepts — the latter is not implemented
  yet.

## Publication Execution Layer

Once a publication package is prepared (Milestone 8), Milestone 9 creates a small, mutable runtime record — the **publication execution** — that tracks the *act of attempting to publish* that package. `src/execution/` (`models.py`, `errors.py`, `store.py`, `repository.py`, `manager.py`, `validation.py`, `service.py`) is a new, independent domain: it never rewrites the package, never calls Claude, and never calls Instagram or any other platform. This milestone only ever creates one status, `READY_FOR_DISPATCH` — nothing here dispatches anything.

### Package vs. execution: an architectural separation, not a convention

```
Publication Package   (src/publication/)      Publication Execution   (src/execution/)
immutable content contract                    mutable runtime state
publications/<workflow_id>/<output_id>.json   executions/<publication_id>.json
never rewritten once READY_FOR_PUBLISHING      created, and (from a future milestone on)
                                               transitioned as dispatch is attempted
```

`ExecutionDocument` (models.py) simply has **no field** capable of holding a caption, a hashtag, a prompt, a Claude response, a Telegram callback, a platform response, or a platform post ID — so there is no code path anywhere in this domain that could write runtime-attempt state back onto a `PublicationPackage`, and no code path that lets execution creation mutate the package it reads from. This is recorded as [DECISIONS.md](DECISIONS.md) entry 9, since it's a genuinely new architectural decision (not just an extension of an existing one).

### Execution lifecycle: four values declared, one reachable

```python
class ExecutionStatus(str, Enum):
    READY_FOR_DISPATCH = "READY_FOR_DISPATCH"      # <- only this one is ever constructed in Milestone 9
    DISPATCH_IN_PROGRESS = "DISPATCH_IN_PROGRESS"  # reserved for the future dispatch milestone
    DISPATCH_FAILED = "DISPATCH_FAILED"            # reserved
    COMPLETED = "COMPLETED"                        # reserved
```

The brief's own illustrative "Package READY_FOR_PUBLISHING → Execution PENDING → Execution IN_PROGRESS → Execution READY_FOR_DISPATCH" diagram uses status names (`PENDING`) that don't appear in its own suggested enum — read, per this project's established precedent for illustrative-shorthand diagrams (see Milestone 7's `EDITING_CONTENT` naming mismatch), as "the flow eventually lands on `READY_FOR_DISPATCH` with the package untouched throughout," not as three separate real transitions this milestone performs. Execution creation goes directly from "doesn't exist" to `READY_FOR_DISPATCH` in one atomic create — no separate intermediate write — exactly mirroring Milestone 7's own precedent of never persisting a separately-observable `APPROVED` snapshot when the real system has no distinct content for it.

### Deterministic identity: via storage key, not a hashed ID

Of the two approaches the brief allowed, `execution_id` is a random `exec_<uuid4().hex>` — like every other domain's own internal ID (`draft_id`, `plan_id`, `publication_id`) — while the *storage key* itself, `executions/<publication_id>.json`, is what provides deterministic lookup and idempotency. This exactly mirrors how `DraftManager`/`PublicationManager` already provide "for free" deterministic lookup via a composite key, just simpler here: a single-segment key, since `publication_id` alone is already a globally unique join key (a `pub_<uuid4().hex>`) — no `workflow_id` nesting is needed, unlike drafts/publications, which key on `(workflow_id, output_id)` because a single workflow can have multiple outputs.

### A single `schema_version` field, not two

Every other domain in this codebase (`WorkflowDocument`, `DraftDocument`, `ContentPlanDocument`, `PublicationPackage`) carries both a config-supplied `schema_version` (paired with an evolving Claude prompt/response shape) and a code-owned `version` (the envelope version). `ExecutionDocument` deliberately collapses these into one field, named `schema_version` (matching the brief's own suggested shape) — there is no independent AI-generated content schema to track here at all, so one code-owned version constant (`CURRENT_DOCUMENT_VERSION`, compared against `SUPPORTED_DOCUMENT_VERSIONS`) is sufficient. This is **not** a new environment-configurable value — no `EXECUTION_SCHEMA_VERSION` config variable exists.

### Package resolution never trusts the cached reference alone

`ExecutionService.create_execution()` uses `workflow.publication` (Milestone 8's lightweight reference) only to *locate* the package (`workflow_id`/`output_id`) — the actual `PublicationPackage` is always re-loaded via `PublicationManager`, and its own `status`/`channel`/`publication_id` are what's actually checked. Execution is never created from a package that isn't independently confirmed `READY_FOR_PUBLISHING`, and a linkage mismatch (loaded package's `publication_id` doesn't match the workflow's own reference) is treated as a data-consistency signal, not silently accepted.

### Publisher resolution: a separate, narrow mapping

`resolve_publisher()` maps a `PublicationChannel` to a publisher identifier string (`instagram` → `"instagram"`, today) via its own code-owned `_PUBLISHER_BY_CHANNEL` table — deliberately not just reusing the channel value directly, even though they're identical today. `channel` is data-plane identity (decided at publication-preparation time); `publisher` is an *adapter-selection* identifier a future publisher registry resolves to an actual implementation. This indirection is exactly what lets a later milestone register a different publisher for the same channel (or vice versa) without changing this schema — see "Recommended scope for Milestone 10" below.

### Idempotency and concurrent creation

`ExecutionManager.find_existing()` is checked first, before anything else — if a record already exists for the exact `publication_id`, it's reused verbatim, never rebuilt. If two concurrent calls both pass that check and race on the conditional create (`IfNoneMatch="*"`), the loser gets `ExecutionConcurrentModificationError`; the service catches that specific case and re-reads `find_existing()` once more to reuse the winner's record — "2 requests → 1 execution," per this milestone's own requirement — rather than surfacing the race as a user-facing failure.

### Pointer-clearing: deferred one link further

Milestone 8 established that the active conversation pointer should stay alive until the whole approve → prepare chain is finally resolved, so `/retry`/`/status` keep finding the workflow through the ordinary active-pointer path. Milestone 9 extends that chain by one more link: `PublicationPreparationService.prepare_publication()` gained a `clear_pointer_on_success` parameter (default `True`, fully backward-compatible with every existing Milestone-8 test) so the handler-level chain (`_run_publication_preparation_and_reply()` → `_run_execution_creation_and_reply()`) can pass `False` at the preparation step and let execution creation — now the last link — clear the pointer instead. A *permanent* execution-creation failure also leaves the pointer untouched (mirroring Milestone 8's identical permanent-failure precedent), since the underlying package and workflow remain completely valid.

### Restart recovery

A crash between "package persisted" and "execution created" leaves `workflow.publication.status == "READY_FOR_PUBLISHING"` with no execution record yet — `/retry`'s `"publication_execution"` branch (or a fresh `"publication_preparation"` retry, which safely re-chains into execution creation via its own idempotent reuse path) re-drives `create_execution()` from that same durable signal, never regenerating or touching the package, never calling Claude.

### `/retry`

`/retry` gained a seventh pending-operation value, `"publication_execution"` — lowercase snake_case, consistent with the existing six — dispatched to `_run_execution_creation_and_reply()` directly (skipping a redundant re-run of an already-succeeded `prepare_publication()`), never reapproving the draft or asking the user to review it again.

### Telegram experience

Execution-creation failures are **not** distinctly surfaced to the user this milestone: the reply is always `APPROVE_AND_PREPARED_MESSAGE` regardless of how execution-record creation goes, since the publication package genuinely is prepared either way, and there is nothing yet for the user to be meaningfully blocked on (dispatch doesn't exist until a future milestone). A failure is logged and left for `/retry` to resolve silently. `/status` was **not** given new execution-specific outcomes this milestone, for the same reason — see "Known limitations."

### Logging

Logged: execution creation requested, package resolved, existing execution reused, execution created, concurrent-creation race reconciled, validation success/failure, retry scheduled — safe identifiers only (`execution_id`, `publication_id`, `workflow_id`, `channel`, `publisher`, `status`). **Never logged**: the caption, hashtags, prompts, a platform response, secrets, or tokens.

### New dependency

None. No Instagram/social-platform SDK, no OAuth library, no queue/worker/scheduler — this milestone never connects to any external platform.

### Known limitations

- Execution-creation failures have no distinct Telegram-facing message or `/status` outcome this milestone (see "Telegram experience" above) — a future milestone that makes dispatch itself meaningful to the user should revisit this.
- Only `READY_FOR_DISPATCH` is ever reachable — there is no transition/supersede logic in `ExecutionManager` yet, since nothing yet causes an execution to fail or complete. A future milestone's dispatch step will need to add that.
- As with every earlier pipeline stage, a retryable failure has no automatic re-trigger beyond `/retry`.

### Recommended scope for Milestone 10

Per this milestone's own architectural recommendation: keep the future publisher interface generic — a simple contract such as `Publisher.publish(execution, publication_package) -> PublicationResult` — so Milestone 10 only needs to implement an `InstagramPublisher` conforming to it, while future `LinkedInPublisher`/`ThreadsPublisher`/`WebsitePublisher` adapters can conform to the same interface without changing anything in `src/execution/` or `src/publication/`. `ExecutionStatus`'s three reserved values (`DISPATCH_IN_PROGRESS`/`DISPATCH_FAILED`/`COMPLETED`) and `ExecutionDocument.attempt` already anticipate this — Milestone 10's job is to add the actual dispatch call, the status transitions, and (separately, never onto the package) wherever a platform response/post ID needs to live.

## Instagram Publisher Adapter and Dispatch Lifecycle

Once an execution record reaches `READY_FOR_DISPATCH` (Milestone 9), Milestone 10 adds the first real external platform integration: `src/publisher/` (a generic, platform-neutral contract) and `src/publisher/instagram/` (the Meta Graph API adapter implementing it), orchestrated by `src/execution/dispatch_service.py`. This is the system's first genuinely irreversible, publicly-visible side effect — every design choice below is oriented around never publishing more than once.

### Documentation reviewed and key facts verified

Official Meta for Developers documentation only (`developers.facebook.com/docs/instagram-platform/...`) — no blog tutorials, no Stack Overflow, no deprecated Instagram Basic Display docs, no Facebook Reels docs applied to Instagram Reels. Verified: the three-step publishing sequence (create container → poll `status_code` → publish), the five container `status_code` values (`EXPIRED`/`ERROR`/`FINISHED`/`IN_PROGRESS`/`PUBLISHED` — `PUBLISHED` is a real, queryable terminal state, not just an application assumption), that `media_publish`'s returned `id` is a *different*, durably-distinct identifier from the container id, the two supported login/auth models and their permission sets, the current Graph API version (`v25.0`, released 2026-02-18 per Meta's own versions page), and common Graph API error codes (1/2/4/17/341/368 retryable-throttling; 10/190/200-299 permission/auth; 100/506 permanent-invalid). See CLAUDE.md for the exact review date.

### Repository media reality verified before implementing the API mapping

Per this milestone's own explicit instruction not to infer behavior from the output type name alone: `src/ai/analysis_service.py`'s `_ANALYZABLE_MEDIA_TYPES` currently analyzes `image/jpeg` only, so the only reachable approved draft today is a single-image post — even though the output type is named `instagram_reel_caption`. **This required zero upstream contract change to resolve**: `src/publisher/instagram/models.py`'s `resolve_media_mode()` derives the real Instagram container mode (`IMAGE` vs `REELS`) from `MediaAssetReference.media_type` — a field the publication package already carries — never from `output_type` (which is, and remains, used only for *channel* resolution). `instagram_reel_caption` + `image/jpeg` therefore correctly publishes as an Instagram Feed image post today; `video/mp4` would correctly publish as a Reel the day the analysis gate is extended, with no change needed here.

### Generic publisher contract

```python
class Publisher(Protocol):
    def advance(self, execution: ExecutionDocument, publication: PublicationPackage) -> PublisherStepResult: ...
```

Checkpoint-oriented (`advance()`), not a monolithic `publish()` — Instagram's flow has multiple recoverable steps, and one `advance()` call performs exactly one durable step, dispatched on `execution.checkpoint`. `PublisherStepResult`/`PublisherResult`/`PublisherFailure` (`src/publisher/models.py`) are narrow, closed dataclasses with no field wide enough to hold a raw HTTP response, an access token, or a full platform error body — the "never expose raw HTTP-library objects, never return secrets" requirement is structural, not just a convention. `InstagramPublisher` never persists the execution itself and never sends a Telegram message — `ExecutionDispatchService` does both, per the brief's own "publisher returns, service persists" separation.

### Publisher registry

`PublisherRegistry` (`src/publisher/registry.py`) maps a `publisher` string (already resolved deterministically from `channel` in Milestone 9) to a concrete adapter. Unknown publishers raise `UnknownPublisherError`, never a silent fallback to Instagram; handlers never instantiate a publisher directly.

### Execution schema v2

`ExecutionDocument` gained (all optional, all defaulted so a Milestone-9 v1 document reads back unchanged): `checkpoint` (`DispatchCheckpoint`, defaults `NOT_STARTED`), `lease`, `platform_state`, `result`, `failure`, `dispatch_authorization`. `SUPPORTED_DOCUMENT_VERSIONS` grew to `{1, 2}`.

```python
class DispatchCheckpoint(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    CONTAINER_CREATED = "CONTAINER_CREATED"
    CONTAINER_PROCESSING = "CONTAINER_PROCESSING"
    CONTAINER_READY = "CONTAINER_READY"
    PUBLISH_REQUESTED = "PUBLISH_REQUESTED"
    PUBLISHED = "PUBLISHED"
    VERIFIED = "VERIFIED"
    # MEDIA_ACCESS_PREPARED is declared for schema completeness but never
    # persisted as a distinct step by this adapter — preparing a
    # temporary media URL has no durable Meta-side counterpart, so
    # recovering from NOT_STARTED and from MEDIA_ACCESS_PREPARED would do
    # identical work. A future publisher whose media-access step *is*
    # durable can use it properly.
```

`status` (the four Milestone-9 values, all now reachable) is the overall lifecycle; `checkpoint` is the finer-grained "last durable platform step" recovery needs — a `DISPATCH_FAILED` execution's checkpoint tells a retry exactly how far it got (e.g. `CONTAINER_CREATED` means a real container exists and must be reused, never recreated).

### At-most-once dispatch: the three mechanisms

1. **OCC-protected claim.** `ExecutionManager.claim_dispatch()` conditionally transitions `READY_FOR_DISPATCH`/an expired `DISPATCH_IN_PROGRESS` lease into an owned, leased `DISPATCH_IN_PROGRESS` state. A losing conditional write reloads and re-evaluates; it never proceeds on stale state.
2. **Checkpoint-before-call for the one irreversible step.** `dispatch_service.py`'s main loop special-cases the `CONTAINER_READY` transition: it durably persists `PUBLISH_REQUESTED` *before* calling `publish_media()`, handing the publisher the *pre-transition* in-memory snapshot (still `CONTAINER_READY`) so the actual call happens exactly once. Any later `advance()` call that loads a fresh execution already showing `PUBLISH_REQUESTED` is statically routed by `InstagramPublisher` to reconciliation-only logic — the code path that would call `publish_media()` again is simply unreachable once that checkpoint is persisted.
3. **Conservative ambiguous-outcome handling.** A timeout/connection-loss/unparseable-body on `publish_media()` raises `PublisherAmbiguousError`, marked retryable so exactly one thing can happen on a later call: reconciliation via a single `get_container_status()` read. `PUBLISHED` → success (with `external_media_id: null` — a documented, accepted limitation, since Meta exposes no container→media reverse lookup). `ERROR`/`EXPIRED` → safe, permanent failure (nothing was posted; checkpoint resets to `NOT_STARTED` so a future attempt creates a fresh container). Still `FINISHED`/`IN_PROGRESS` → genuinely still ambiguous — marked a **permanent, non-retryable** failure requiring **manual operator intervention**, never a second blind `publish_media()` call.

This system does not claim exactly-once delivery — Meta's API provides no idempotency key for `media_publish` that would make that claim honest. It claims, and enforces, the strongest achievable **at-most-once** guarantee.

### Container-creation ambiguity is treated differently, on purpose

An ambiguous/timed-out `create_media_container()` call is ordinarily retryable — a retry simply creates a fresh container. An orphaned, never-published container is harmless: it silently expires in 24 hours. Duplicating a container costs nothing visible; duplicating a *publish* creates a second, public Instagram post. The two are not symmetric risks, so they are not handled identically (see `src/publisher/instagram/publisher.py`'s own docstring).

### Attempt semantics

`attempt` increments exactly once per externally-meaningful dispatch attempt: a fresh claim from `READY_FOR_DISPATCH`, or a retry from a *retryable* `DISPATCH_FAILED` state. It does **not** increment for: status polling within one dispatch, recovering an expired lease (resuming the same attempt), or ambiguous-outcome reconciliation (a read-only recovery of the same prior attempt) — verified directly in `tests/test_execution_dispatch_service.py`.

### Explicit publish authorization

Approval (Milestone 7) does not authorize publishing — PRODUCT.md/ROADMAP.md were checked first: ROADMAP.md's Phase 3 goal says "actual publishing to Instagram on Approve" but explicitly defers "exact UX to be decided at this milestone." Given this milestone's own repeated emphasis that an irreversible, public side effect must never be assumed automatic, the UX decision made here is: a dedicated **"Publish to Instagram"** inline button (attached to the same message that announces "prepared for publishing," only when `INSTAGRAM_PUBLISHING_ENABLED=true`) is the sole trigger. `dispatch_authorization` (`{authorized_at, authorized_by_telegram_user_id, publication_id, execution_id}`) is persisted before the first Meta call; `/retry` can resume an already-authorized dispatch but can never create the first authorization (`MissingDispatchAuthorizationError` otherwise).

### Pointer-clearing: deferred one link further, again

Continuing the exact pattern from Milestone 8→9: when Instagram publishing is enabled, `ExecutionService.create_execution()`'s `clear_pointer_on_success` is passed `False` (chaining into a further dispatch step), and `ExecutionDispatchService` clears the pointer only once dispatch finally succeeds — never on a retryable failure, and (mirroring Milestone 8/9's identical permanent-failure precedent) never on a permanent failure either, so `/status` can still describe it and `/retry` can still resolve the workflow through the ordinary active-pointer path. When publishing is disabled, execution creation clears the pointer immediately, exactly as Milestone 9 originally specified — there is no further step to chain into.

### Telegram experience

"Publish to Instagram" → *"Publishing to Instagram…"* → one of: *"Published to Instagram successfully."* (+ permalink, only when available — its absence never fails an otherwise-successful publication), *"Instagram could not complete the publication right now. Your approved content is safe and this dispatch can be retried."*, or *"Instagram could not publish this content. Your approved draft and publication package are still safe."* Never a raw Meta error. `/status` distinguishes six outcomes (ready/in-progress/processing-media/retryable-failed/permanently-failed/completed) and never says "published" before the execution is durably `COMPLETED`.

### Feature flag / default-disabled behaviour

`INSTAGRAM_PUBLISHING_ENABLED` defaults to `false`. Disabled: authoring, approval, publication preparation, and execution creation all work with zero Instagram configuration — `INSTAGRAM_ACCOUNT_ID`/`META_ACCESS_TOKEN` are not required to start the bot at all, and no "Publish" button is ever shown. Enabled: both become required, fail-fast at startup.

### Security and redaction

Never persisted: access tokens, authorization headers, presigned media URLs, raw Meta responses/error bodies, captions/hashtags inside execution documents (structurally impossible — no such field exists). Never logged: the same, plus the caption/hashtags/CTA themselves. `validate_dispatch_dict()` (`src/execution/validation.py`) is a defensive backstop scanning every dispatch-lifecycle dict for banned key names before persistence.

### Automated testing

Every test uses a fake Meta HTTP client / scripted fake publisher and a fake S3 client where the real Store→Repository→Manager stack is exercised — no real network calls, no real Instagram post, no production credentials anywhere in the codebase or test suite.

### New dependency

`httpx` — already a transitive dependency (via `anthropic` and `python-telegram-bot`), now pinned directly since `src/publisher/instagram/client.py` imports it explicitly. No new third-party dependency was introduced.

### Known limitations

- `external_media_id`/`permalink` are unrecoverable when a publication is confirmed only via ambiguous-outcome reconciliation (Meta exposes no container→media reverse lookup) — the execution still correctly reaches `COMPLETED`, but with a null `external_media_id`.
- A publish outcome that remains genuinely ambiguous after one reconciliation check requires manual operator intervention — this system deliberately never auto-retries past that point.
- Only a single-image Instagram Feed post is reachable in practice today (see "Repository media reality" above) — Reels are implemented and tested but not yet reachable end-to-end pending the upstream analysis-gate limitation.
- Carousel posts, Stories, and all other Instagram content types remain out of scope.

## Authoring Contract Consistency (Milestone 11A)

### The latent bug

`src/ai/content_plan_models.py`'s `OUTPUT_TYPE_REGISTRY` has always
registered two distinct Instagram output types: `instagram_reel_caption`
(`supports_generation=True`) and `instagram_feed_caption`
(`supports_generation=False` — a placeholder for a generator that doesn't
exist yet). Nothing before this milestone prevented Claude's content plan
from selecting `instagram_feed_caption` as priority-1 output — which would
reach `src/ai/draft_generation_service.py` and fail there, permanently,
with no generator registered for it. This was discovered during
Milestone 11's planning investigation into the Feed/Reel contract
mismatch, not reported by a user or a test failure.

### The fix

`src/ai/content_plan_validation.py`'s existing
`_validate_output_types_and_priorities()` — the single place every planned
output is already checked — now also rejects any plan whose priority-1
output's registry entry has `supports_generation=False`, via
`ContentPlanValidationFailedError` (the same error type this function
already raises for every other structural planning violation). Because
that error is an `AnalysisError` subclass, it automatically inherits
`content_planning_service.py`'s existing one-bounded-regeneration-attempt-
then-permanent-failure retry behavior — **zero new service-layer code was
needed**, only a stricter validation rule at the layer that already
gates every plan before it's ever persisted or acted on.

Unsupported output types are never deleted or unregistered — they remain
selectable at any non-priority-1 slot, preserving the registry's future
extensibility (a later milestone can implement a Feed-caption generator
without this validation rule needing to change).

### Tests

`tests/test_ai_content_plan_validation.py` verifies: a plan whose
priority-1 output supports generation still validates normally (no
regression); a plan with `instagram_feed_caption` at priority 1 is
rejected; the same output type remains acceptable at priority 2+;
`instagram_feed_caption` remains a registered, selectable output type
(never removed); and the resulting validation failure is exactly as
informative as any other planning validation failure (same error type,
same retry path).

## Explicit Placement Contract (Milestone 11A)

### The problem

`output_type=instagram_reel_caption` does not, on its own, reliably
describe where content will actually appear on Instagram. Before this
milestone, `src/publisher/instagram/models.py`'s `resolve_media_mode()`
correctly derived the real container mode (`IMAGE` vs `REELS`) from the
approved media's own MIME type at *publish time* — but nothing recorded
that resolution anywhere durable, and the reviewer-facing label ("Instagram
Reel caption") misleadingly implied every approved post becomes a Reel,
even when — as is true for every post reachable today, a single approved
JPEG — it will actually publish as a Feed image.

### Option B: an explicit, persisted placement record

Rather than splitting `output_type` itself into separate Feed/Reel
variants (which would have required changes to Milestones 5/6's
already-shipped planning/generation schemas and prompts), Milestone 11A
introduces a small, additive record next to the existing output type:

```json
{"channel": "instagram", "placement": "feed", "media_mode": "single_image"}
```

`src/publication/models.py` adds `Placement` (`feed`/`reel`) and
`MediaMode` (`single_image`/`video`) enums, a `PublicationPlacement`
dataclass, and `resolve_placement(channel, media_type)` — the single,
deterministic, code-owned function that maps a MIME type to a placement
(`image/jpeg → feed/single_image`, `video/mp4 → reel/video`; never asks
Claude, never infers from content text). `src/publication/service.py`
calls it exactly once, immediately after building the package's media
references, and persists the result on the package as
`PublicationPackage.placement` — publication schema bumps to **v2**
(`SUPPORTED_DOCUMENT_VERSIONS={1, 2}`) to carry it.

### Consuming placement: never re-inferred

`src/publisher/instagram/publisher.py`'s `InstagramPublisher` no longer
calls `resolve_media_mode(media.media_type)` at all. It calls
`publication.resolved_placement()` — the one method every consumer
(publisher, a future preview renderer, a future analytics reader) uses
to get a package's placement — and maps that already-resolved
`Placement` to an `InstagramMediaMode` via the rewritten
`resolve_instagram_media_mode(placement)` in `models.py`. The publisher
never touches a raw MIME type for this decision anymore.

### Lazy migration: immutability fully preserved

`PublicationPackage.resolved_placement()` returns the persisted
`placement` field for every package created since schema v2. For a v1
package (created before this field existed — `placement=None` on disk),
it derives the same answer **in-memory only**, from that package's own
already-persisted `channel` and `media[0]['media_type']` — and never
writes the result back to storage. A v1 package remains, on disk, exactly
the bytes it always was; only the in-memory object this method returns
differs. This is the "lazy migration" the milestone's brief calls for:
correct behavior for historical packages with zero migration script,
zero S3 write, and zero risk to package immutability.

### Reviewer terminology

The Telegram-facing label reviewers see (`OUTPUT_TYPE_REGISTRY[INSTAGRAM_
REEL_CAPTION].display_name`, shown in `/status`'s "Content plan ready"
message and in `src/ai/errors.py`'s edit-rejection message) changed from
"Instagram Reel caption" to **"Instagram caption"** — a presentation-only
change. Claude's planning-time system prompt still sees the distinct
`output_type` enum values (`instagram_reel_caption` vs
`instagram_feed_caption`) regardless of display-name wording, so planning
behavior is unaffected. No stored authoring semantics changed — only the
human-readable label a reviewer sees.

### Tests

`tests/test_publication_models.py` verifies: `resolve_placement()` maps
JPEG to feed/single_image and MP4 to reel/video (and rejects unknown MIME
types); `PublicationPlacement` round-trips through `to_dict()`/
`from_dict()`; a schema-v2 package persists and round-trips its
`placement` field, and `resolved_placement()` returns the *persisted*
value rather than re-deriving it even when doing so would give a
different answer; a historical v1 package (`placement=None` on disk)
still loads via `from_dict()` and `resolved_placement()` correctly
derives feed for JPEG / reel for MP4; deriving a placement for a v1
package never mutates the package or its `to_dict()` output.
`tests/test_publication_service.py` verifies the service persists the
correct placement dict via `PublicationManager.create_publication()`'s
`placement` kwarg for both a JPEG-media and an MP4-media workflow.
`tests/test_publisher_instagram_models.py` verifies
`resolve_instagram_media_mode()` takes only a `placement` parameter —
structurally, there is no `media_type` parameter for it to re-infer from.

## Instagram Operational Diagnostics (Milestone 11A)

### Purpose

`/instagram_status` (note: implemented with an underscore, not the
hyphen written in the milestone brief — Telegram bot commands may only
contain letters, digits, and underscores) answers "is Instagram
publishing actually usable right now" without ever creating a media
container, polling one, or calling `publish_media()`. It requires no
active workflow — it reports the *system's* readiness, not anything
about a specific post — and never creates or mutates an execution or
publication record.

### Health model: three concepts, never conflated

- **Application healthy** — the bot process is up and able to answer a
  command at all. Always `true`; never depends on Meta being reachable,
  configured, or enabled.
- **Instagram configured** — the two required credentials
  (`INSTAGRAM_ACCOUNT_ID`/`META_ACCESS_TOKEN`) are present. `Config.
  from_env()` already fails fast at startup if publishing is enabled
  without them, so in practice this is only ever `false` while
  publishing is also disabled — but the two facts are reported as
  separate fields, never collapsed into one.
- **Instagram ready** — enabled AND configured AND the access token is
  currently valid AND it carries the scopes content publishing requires
  AND the configured account can actually be looked up. Only "ready" ever
  depends on a live Meta response.

### Diagnostic sequence (read-only, short-circuiting)

`src/publisher/instagram/diagnostics.py`'s `InstagramDiagnosticsService.
check_status()`: publishing enabled? → configuration present? → token
validation → permission validation → Instagram account verification →
overall readiness. It stops at the first failing stage — a later Meta
operation is never called once an earlier one has already failed — and
never raises; any Meta call failure becomes a diagnostic result field.

### Meta client additions (read-only only)

`src/publisher/instagram/client.py`'s `MetaHttpClient` gains exactly two
new operations, both plain `GET` requests, neither able to create,
modify, or publish anything:

- `debug_token()` — Meta's standard token-introspection endpoint (`GET
  /debug_token`), returning `data.is_valid`/`data.scopes`. The configured
  access token is passed as both `input_token` and `access_token` (this
  repository holds no separate app access token for a true third-party
  introspection call).
- `get_account_identity()` — fetches the configured Instagram account's
  own `id`/`username`, the minimum needed to confirm the credentials
  resolve to a real, reachable account.

No write operation (`create_media_container`/`publish_media`) is ever
called by diagnostics.

### Telegram experience

```
Instagram diagnostics

Status: Ready to publish
Token: Valid
Permissions: Verified
Account: Verified (@nrc_official)
Publishing: Enabled
```

Never shown: the access token, a raw Graph API response, the full
Instagram account id, a presigned URL, a caption, or hashtags — only the
account's `@username` when verification succeeds.

### Configuration

No new environment variable was introduced. Diagnostics reuse
`INSTAGRAM_PUBLISHING_ENABLED`/`INSTAGRAM_ACCOUNT_ID`/`META_ACCESS_TOKEN`/
`META_GRAPH_API_VERSION`/`META_REQUEST_TIMEOUT_SECONDS` — the exact same
configuration `InstagramPublisher` already uses. Startup still succeeds
with `INSTAGRAM_PUBLISHING_ENABLED=false`, and `/instagram_status` reports
that cleanly (`Status: Not ready — Instagram publishing is not enabled`,
`Publishing: Disabled`) rather than erroring.

### Logging

Only ever logs the stage reached and its boolean outcome (diagnostics
started, configuration status, token valid/invalid, permissions
verified, account verified, readiness result) — never the access token,
a raw Graph API response, a presigned URL, a caption, or hashtags.

### Tests

`tests/test_publisher_instagram_client.py` verifies `debug_token()`/
`get_account_identity()` issue the expected `GET` requests and never a
`POST`. `tests/test_publisher_instagram_diagnostics.py` verifies: the
disabled and not-configured paths report correctly without any Meta call;
an invalid token stops before any later stage; missing permissions stop
before the account check; every stage passing reports `instagram_ready=
True` with the verified username; `check_status()` never raises on a Meta
failure and never calls `create_media_container`/`publish_media`/
`get_container_status`. `tests/test_handlers.py` verifies `/instagram_status`
denies an unauthorized user, requires no workflow resolution, and never
calls the execution/publication managers.

### Known limitations

- Token introspection uses the token to inspect itself (no separate app
  access token is configured in this repository) — this is a narrower
  guarantee than Meta's full app-token-based `/debug_token` introspection,
  but is sufficient to distinguish a valid token from an invalid/expired
  one for this diagnostic's purpose.
- `/instagram_status` reports readiness at the moment it's called — it is
  a point-in-time check, not a continuously monitored health endpoint.

## Controlled Instagram Live Validation (Milestone 11B)

### Core safety rule

Ordinary draft approval is never sufficient to trigger a real Instagram
publication through this flow. Controlled live validation is
operator-only, explicitly initiated, separately confirmed, tied to one
exact execution/account/package version, protected from stale
confirmations, and structurally unable to bypass any Milestone 10
dispatch safeguard — because it never reimplements dispatch. **There is
no second publish implementation.** `ControlledLiveValidationService.
confirm()` (`src/execution/live_validation.py`) calls the exact same
`ExecutionDispatchService.authorize_and_dispatch()` the ordinary
"Publish to Instagram" button already calls; no code in this milestone
imports `InstagramPublisher` or `MetaHttpClient` directly (verified by a
dedicated structural test).

### Durable state, extending the existing execution domain

A Telegram confirmation button is transient — Telegram can drop or
duplicate a message. The *authorization* it leads to is not:
`build_preview()` persists a durable `AWAITING_CONFIRMATION` record
directly on the target execution document's own
`metadata["live_validation"]`, written through a new, narrow,
OCC-protected manager method (`ExecutionManager.set_live_validation_state()`)
that reuses the execution domain's *existing* etag/conditional-write
pattern — no wholly separate S3 namespace or persistence domain was
introduced (see DECISIONS.md entry 12 for why this was the deliberate
choice). The record captures: which operator, which publication/
execution, a short deterministic digest of the entire publication
package, a masked Instagram account fingerprint, the execution's
status/checkpoint/attempt at preview time, a one-time confirmation
token, and an expiry (5 minutes).

### Operator authorization: a second, independent gate

`INSTAGRAM_LIVE_TEST_OPERATOR_IDS` (optional, empty by default — see
`.env.example`) is checked **in addition to**, never instead of, the
general `TELEGRAM_ALLOWED_USER_IDS` allowlist every other command
already enforces. An ordinary authorized bot user is never automatically
trusted to perform a real live publication. Every
`ControlledLiveValidationService` method re-checks this independently of
the Telegram-layer `@restricted` check (defense in depth) — even if a
future handler forgot the check, the service itself still refuses a
non-operator.

### Command and callback design

`/instagram_test_publish` (implemented with an underscore — Telegram
commands reject hyphens, same as `/instagram_status`) never publishes
immediately; it locates an eligible candidate and renders a confirmation
preview. Three callback actions live in their own namespace, deliberately
distinct from the ordinary `dispatch:...` callback so the two can never
be confused: `ilv:s:<publication_id>` (select, only reachable with
multiple candidates), `ilv:c:<publication_id>:<token>` (confirm),
`ilv:x:<publication_id>:<token>` (cancel) — all comfortably under
Telegram's 64-byte callback_data ceiling.

### Eligibility: the existing ownership-safe pointer, never a new S3 scan

Eligible candidates are resolved through the same `resolve_active_workflow()`
mechanism `/status`/`/cancel`/`/retry`/the ordinary dispatch button all
already use — never a new S3 listing capability (deliberately avoided;
see this milestone's own explicit instruction against scanning arbitrary
S3 keys). Because only one workflow can ever be active per Telegram user
at a time (Milestone 3.5's own upload-refusal rule), there is
structurally at most one eligible candidate reachable this way today —
the "multiple eligible" selection-list code path is still implemented
(for forward compatibility, and exercised by a synthetic unit test) but
is not reachable through the real service under the current
architecture. See "Known limitations" below.

Eligibility requires: a `READY_FOR_PUBLISHING` package, an execution that
is not `COMPLETED`, not under an active dispatch lease, not blocked by an
unresolved ambiguous outcome (checked regardless of that failure's own
`retryable` flag), a resolved placement of Feed + single JPEG image
(Reels are out of scope for this milestone even though the Instagram
adapter supports them internally), and a fresh `/instagram_status`-
equivalent diagnostic reporting `instagram_ready=True`.

### Confirmation preview and re-validation, twice

`build_preview()` checks eligibility, freshness, and Instagram readiness
once. `confirm()` independently re-checks *every one of those same
facts again* — operator identity, confirmation token match, expiry,
one-time consumption, execution status/checkpoint/attempt (exact-match
against what was captured at preview time), the publication package's
digest (a SHA-256 of the entire persisted package — any change at all is
detected), the masked Instagram account fingerprint, and Instagram
readiness — immediately before ever calling `authorize_and_dispatch()`.
Any mismatch raises a specific `LiveValidationError` subclass and
dispatches nothing; the operator must request a fresh preview. The
preview itself never shows the access token, a raw Graph API response,
the full Instagram account id (only a masked `••••1234` suffix), a
presigned media URL, or a raw caption rewrite — only the already-approved
caption is used, unchanged.

### One-time confirmation consumption

Consuming a confirmation (`AWAITING_CONFIRMATION` → `CONFIRMED`) is
itself an OCC-protected write using the exact etag loaded at the start of
`confirm()`. Two concurrent confirmation callbacks can never both reach
dispatch: whichever loses the race (either because the other's write
already landed first, changing the persisted `status` away from
`AWAITING_CONFIRMATION` before this call even re-reads it, or because
both loaded the same etag and only one conditional write can win) raises
`LiveValidationConfirmationNotFoundError` and calls
`authorize_and_dispatch()` zero times. A repeated confirm callback for an
already-consumed token gets the same safe "already used or expired"
response, with zero further Meta calls.

### Dispatch reuse and the live-validation marker

`confirm()`'s only interaction with dispatch is calling
`ExecutionDispatchService.authorize_and_dispatch(..., dispatch_context=
{...})` — a new, optional parameter that, *only the one time an
authorization is first created* (mirroring that method's own existing
idempotency), merges a small, safe, static provenance record into the
`dispatch_authorization` dict: `mode="controlled_live_validation"`,
the operator's Telegram user id, the confirmation timestamp, and the
masked account fingerprint. This is the only change `dispatch_service.py`
needed for this milestone — no new checkpoint, no new execution status,
no new write operation, no weakened check.

### Diagnostics requirement

Both `build_preview()` and `confirm()` call `InstagramDiagnosticsService.
check_status()` fresh (a live, read-only GET-only call) rather than
trusting a cached result — per this milestone's own preference for a
fresh diagnostic over a time-limited cache for the first live
validation. A diagnostics failure at either point produces zero Meta
write calls and a specific, distinguishable reason (disabled / not
configured / invalid token / missing permission / account mismatch /
Meta unavailable) surfaced through the same `InstagramDiagnosticsResult`
Milestone 11A already built — no new diagnostic surface was introduced.

### `/retry` reuses existing semantics, unchanged

No second retry system was built. Once `confirm()` creates a
`dispatch_authorization`, it is — per Milestone 10's own design — a
*standing*, execution-scoped authorization, not a single-use token:
`/retry`'s existing `"publication_dispatch"` branch
(`ExecutionDispatchService.retry_dispatch()`, completely unchanged) can
resume a controlled-live-validation-originated dispatch through an
ordinary retryable pre-publish failure without requiring a new operator
confirmation — verified by a dedicated integration test
(`test_retry_dispatch_resumes_a_controlled_live_validation_authorization_without_a_new_confirmation`).
`/retry` still never blindly republishes past `PUBLISH_REQUESTED` and
never retries an unresolved ambiguous outcome — both guarantees are
Milestone 10's own, untouched by this milestone.

### Result reporting

A completed dispatch reports success with a shortened media id, any
permalink, an explicit "exactly one live publication was attempted," and
a reminder to verify manually on Instagram. An ambiguous outcome is
reported with an explicit "do not retry" warning — never a normal retry
prompt. An ordinary retryable failure explicitly notes the existing
confirmation already covers a `/retry` (no new confirmation needed). A
Telegram delivery failure after a durable completion can never cause a
republish — `/status` always shows the durably `COMPLETED` state
regardless of whether the completion message itself was ever delivered.

### Security and redaction

Never shown or logged: the access token, a raw Graph API response, the
full Instagram account id, a presigned media URL, or the confirmation
token itself in any log line. `set_live_validation_state()`'s dict (and
`dispatch_context`) are scanned against the same banned-key denylist
(`src/execution/validation.py`) every other execution-domain dict
already goes through, defensively, before ever being persisted.

### Testing

Every test uses fakes — a stateful in-memory `ExecutionManager` double,
scripted fake publishers, mocked diagnostics — and a session-wide,
autouse pytest fixture (`tests/conftest.py`) that raises on any real
`httpx.get()`/`httpx.post()` call, so no test can ever reach Meta's real
API even if real credentials happen to be present in the environment a
test runs in. `tests/test_execution_live_validation.py` exercises the
full `confirm()` → `authorize_and_dispatch()` → `Publisher.advance()`
chain against a real (non-mocked) `ExecutionDispatchService`, proving
this milestone genuinely reuses Milestone 10's dispatch machinery rather
than a parallel implementation.

### Known limitations

- The "multiple eligible candidates" selection path is implemented but
  not reachable through the real service today — only one workflow can
  ever be active per Telegram user at a time (see "Eligibility" above).
- Token introspection self-inspects (no separate Meta app access token
  is configured in this repository) — the same accepted limitation
  Milestone 11A's `/instagram_status` already documents.
- Only a single JPEG Feed image is eligible for controlled live
  validation, deliberately — Reels remain out of scope until a later
  milestone explicitly extends this.
- `/instagram_test_publish` reports readiness at the moment it's
  called — it is a point-in-time check, not continuous monitoring.

## Local testing approach (without exposing credentials)

- Never put real credentials in a committed file — only `.env` (git-ignored)
  or your shell environment.
- Use a **non-production** IAM identity and a **non-production** bucket (or
  prefix) for local development, scoped as narrowly as described above, so a
  local mistake can't touch anything that matters.
- The automated test suite (`pytest`) never makes a real Telegram, AWS, or
  Anthropic call — every test mocks the Telegram `Bot`/`Message` objects,
  the boto3 S3 client, and (since Milestone 4A) the `anthropic.Anthropic`
  client at their boundaries (see `tests/test_media_*.py`,
  `tests/test_storage_*.py`, `tests/test_json_object_store.py`,
  `tests/test_workflow_*.py`, `tests/test_conversation_*.py`, and
  `tests/test_ai_*.py`). You don't need any credentials at all to run
  `pytest`.
- To manually test the real upload + analysis path end-to-end, you do need
  a real bot token, real (narrowly-scoped) AWS credentials, and a real
  Anthropic API key in your local `.env` — there's no way around exercising
  the real Telegram/AWS/Anthropic APIs for that. A fake `anthropic.Anthropic`
  client substituted via monkeypatch is enough for a manual integration
  smoke test that doesn't spend real API credits.

## Running locally

```bash
python -m src.main
```

The bot connects to Telegram via long polling — no inbound webhook or public
URL is required for local development. Message it from an allow-listed
Telegram account: send a photo (uploads to S3, creates a workflow record,
sets it as your active post, runs a single-pass Claude analysis, starts
the adaptive clarification conversation, and — once enough context exists
— automatically plans the content and sends a short summary of recommended
outputs) or a video (uploads and creates a workflow record exactly as
before, but analysis/clarification/planning are skipped with a clear
message — see "Supported media types and known limitations" above). If
asked a clarification question, just reply in plain text — no special
formatting needed; the bot may ask another question, or move straight on
to planning, depending on your answer. Then try `/status` (reports its
real state and reference in plain language), `/cancel` (rejects it,
persists that, and clears your active pointer), and `/retry`
(re-attempts the last step — analysis, clarification, or content
planning — if it couldn't complete). `/start` and `/help` are still
static text.

## Running tests

```bash
pytest
```

## Building the Docker image

```bash
docker build -t nrc-social-agent .
```

Run it with your `.env` file (never bake secrets into the image):

```bash
docker run --rm --env-file .env nrc-social-agent
```

## Deploying

Automated via GitHub Actions
(`.github/workflows/deploy-social-agent.yml`, at the monorepo root) to
JustRunMy.App. This repository is a monorepo — the workflow deploys
**only** this directory's contents (via `git subtree split`), never the
rest of the repository, and JustRunMy.App receives `nrc-social-agent/`'s
own contents at its deployment repository's root. See
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the full setup, dry-run
walkthrough, required GitHub secrets, and rollback procedure. Deployment
is independent of Instagram operational validation — see
[docs/INSTAGRAM_LIVE_VALIDATION.md](docs/INSTAGRAM_LIVE_VALIDATION.md)
for that separate, manual, operator-authorized process.

## Project layout

Note: `.github/workflows/deploy-social-agent.yml` (this project's CI/CD
pipeline, Milestone 11D) lives at the **monorepo root**, not inside this
directory — GitHub Actions only discovers workflows in the repository
root's `.github/workflows/`, alongside the NRC Website's own
`deploy-production.yml`.

```
src/
  main.py               application entry point (builds and runs the bot)
  config.py             environment-variable configuration, fail-fast validation
  logging_config.py     stdout logging setup
  access_control.py     TELEGRAM_ALLOWED_USER_IDS enforcement + is_live_test_operator() (11B)
  handlers.py           /start, /help, /status, /cancel, /retry, /instagram_status, /instagram_test_publish, media, text_reply, and fallback handlers
  media/
    types.py            MediaType, TelegramMediaInfo
    errors.py            ingestion exception hierarchy (each with a user_message)
    validation.py        extract + validate incoming Telegram photo/video metadata
    download.py           streams a validated file to a bounded local temp file
    ingestion.py          orchestrates validate -> download -> upload -> workflow -> conversation pointer
  storage/
    client.py            builds the boto3 S3 client from Config
    s3_storage.py         S3 object-key generation and conditional-write upload (media/)
    json_object_store.py  generic S3 JSON-blob CRUD (read/write/exists, conditional writes)
  workflow/
    states.py             WorkflowState enum + TERMINAL_STATES (includes GENERATING_CONTENT since 4B, SHOWING_PREVIEW since 6, EDITING since 7)
    models.py              WorkflowDocument: schema (v6: + clarification_context/pending_question/content_plan/generated_draft/pending_edit/publication), versioning, validation
    errors.py               workflow persistence exception hierarchy (each with a user_message)
    state_store.py          WorkflowStateStore: JsonObjectStore fixed to the state/ prefix
    draft_store.py           DraftStore: JsonObjectStore fixed to the drafts/ prefix (reused by src/ai/draft_repository.py since 6, and draft_version_repository.py since 7)
    repository.py            WorkflowRepository: dict <-> WorkflowDocument, S3-error translation
    manager.py               WorkflowManager: create/load/update-state/conversation-turns/clarification-decisions/generated-draft-reference/editing-lifecycle/approve-save-reject/publication-reference, concurrency
  conversation/
    states.py              ConversationState enum (ACTIVE / IDLE)
    models.py               ConversationDocument: schema, versioning, to_dict/from_dict validation
    errors.py                conversation + resolution exception hierarchy (each with a user_message)
    store.py                 ConversationStore: JsonObjectStore fixed to the conversation/ prefix
    repository.py             ConversationRepository: dict <-> ConversationDocument, S3-error translation
    manager.py                ConversationManager: set/load/clear active workflow, timestamps, concurrency
    resolution.py             resolve_active_workflow(): the single "which workflow is this?" entry point
    lifecycle.py              clear_pointer_if_terminal(): reusable terminal-state pointer clearing (4B)
  ai/
    errors.py               analysis + clarification + content-plan + draft-generation + draft-review/editing exception hierarchy (each with a user_message)
    prompts.py               versioned analysis SYSTEM_PROMPT/USER_PROMPT/RESPONSE_SCHEMA (4A)
    clarification_prompts.py  versioned clarification SYSTEM_PROMPT/RESPONSE_SCHEMA + bounded prompt builder (4B)
    content_plan_prompts.py    versioned planning SYSTEM_PROMPT/RESPONSE_SCHEMA + bounded prompt builder (5)
    draft_prompts.py             versioned per-output-type generation SYSTEM_PROMPT/RESPONSE_SCHEMA + bounded prompt builder (6)
    draft_edit_prompts.py           versioned per-output-type edit SYSTEM_PROMPT + bounded prompt builder, reuses draft_prompts.py's schema (7)
    models.py                 AnalysisResult/AnalysisUsage/AnalysisDocument: schema, versioning, validation (4A)
    clarification_models.py    ClarificationContext/ContextField/ClarificationDecision (4B)
    content_plan_models.py      OUTPUT_TYPE_REGISTRY/PlanStrategy/PlannedOutput/ContentPlanDocument (5)
    draft_models.py               DraftStatus/InstagramReelCaptionContent/DraftDocument (schema v2: + version_number/parent_version_number/edit_instruction_reference since 7), per-output-type content models (6, 7)
    draft_version_models.py         ReviewStatus/CurrentDraftPointer: the mutable current-version pointer (7)
    client.py                  ClaudeClient: the only module allowed to import the anthropic SDK
    parser.py                   defensive parsing/validation of Claude's analysis JSON response (4A)
    clarification_parser.py      defensive parsing/validation of Claude's clarification JSON response (4B)
    content_plan_parser.py        defensive parsing/validation of Claude's content-plan JSON response (5)
    draft_parser.py                 defensive parsing/validation of Claude's draft-generation JSON response, reused unchanged for edit responses (6, 7)
    question_validation.py       code-level quality/safety validation for a generated question (4B)
    content_plan_validation.py    code-level business rules: output count, distinctiveness, final-copy leakage, generic-plan/invented-number heuristics, priority-1-must-support-generation (5, 11A)
    draft_validation.py             code-level business rules: length/hashtag/emoji/CTA, generic-draft/invented-claim heuristics (6)
    draft_edit_validation.py          platform-switch pre-flight scan + deterministic instruction-alignment checks, reuses draft_validation.py's base rules (7)
    analysis_store.py            AnalysisStore: JsonObjectStore fixed to the analysis/ prefix
    analysis_repository.py        AnalysisRepository: dict <-> AnalysisDocument, S3-error translation
    analysis_manager.py            AnalysisManager: idempotency check, conditional create, OCC supersede
    content_plan_store.py          ContentPlanStore: JsonObjectStore fixed to the plans/ prefix (5)
    content_plan_repository.py      ContentPlanRepository: dict <-> ContentPlanDocument, S3-error translation (5)
    content_plan_manager.py          ContentPlanManager: idempotency check, conditional create, OCC supersede, mark-output-generated (5, 6)
    draft_repository.py               DraftRepository: dict <-> DraftDocument over the reused DraftStore, S3-error translation (6)
    draft_manager.py                   DraftManager: idempotency check, conditional create, OCC supersede, keyed by (workflow_id, output_id) (6)
    draft_version_repository.py         DraftVersionRepository: immutable versions/ + current.json over the reused DraftStore (7)
    draft_version_manager.py             DraftVersionManager: Milestone-6-layout migration, OCC version allocation, pointer-status transitions; resolve_current_draft() shared entry point (7)
    analysis_service.py             AnalysisService: orchestrates the full analyze-one-workflow pipeline
    clarification_service.py         ClarificationService: orchestrates the clarification decision cycle (4B)
    content_planning_service.py       ContentPlanningService: orchestrates the content-planning cycle (5)
    draft_generation_service.py        DraftGenerationService: orchestrates the primary-draft generation cycle (6)
    draft_editing_service.py             DraftEditingService: orchestrates the natural-language edit cycle (7)
  publication/
    errors.py               publication-preparation exception hierarchy (each with a user_message) (8)
    models.py                PublicationStatus/PublicationChannel/resolve_channel/PublicationContent/MediaAssetReference/PublicationDraftReference/PublicationApproval/PublicationPackage (schema v2: + placement since 11A)/Placement/MediaMode/PublicationPlacement/resolve_placement (8, 11A)
    store.py                  PublicationStore: JsonObjectStore fixed to the publications/ prefix (8)
    repository.py              PublicationRepository: dict <-> PublicationPackage over PublicationStore, S3-error translation (8)
    manager.py                  PublicationManager: idempotency check, conditional create, OCC supersede, keyed by (workflow_id, output_id) (8)
    builder.py                    build_publication_content()/build_media_references(): mechanical-only mapping from the approved draft (8)
    validation.py                  validate_publication_package(): structural rules + contract-minimality metadata backstop (8)
    service.py                      PublicationPreparationService: orchestrates approval -> channel/version resolution -> build -> validate -> persist (8)
  execution/
    errors.py               execution-creation + dispatch + controlled-live-validation exception hierarchy (each with a user_message) (9, 10, 11B)
    models.py                ExecutionStatus/DispatchCheckpoint/resolve_publisher/ExecutionDocument (schema v2: + checkpoint/lease/platform_state/result/failure/dispatch_authorization since 10) (9, 10)
    clock.py                  Clock/Sleeper protocols + SystemClock/AsyncioSleeper (10)
    store.py                  ExecutionStore: JsonObjectStore fixed to the executions/ prefix (9)
    repository.py              ExecutionRepository: dict <-> ExecutionDocument over ExecutionStore, S3-error translation (9)
    manager.py                   ExecutionManager: idempotency check, conditional create, keyed by publication_id; dispatch-lifecycle writes (authorize/claim/checkpoint/complete/fail) + set_live_validation_state() (9, 10, 11B)
    validation.py                 validate_execution() + validate_dispatch_dict(): structural rules + contract-minimality backstops (9, 10)
    service.py                     ExecutionService: orchestrates package resolution -> publisher resolution -> create -> validate -> persist (9)
    dispatch_service.py             ExecutionDispatchService: claim/lease, checkpoint-before-call ordering, bounded polling, ambiguous-outcome reconciliation, at-most-once dispatch, optional dispatch_context provenance (10, 11B)
    live_validation.py              ControlledLiveValidationService: eligibility, confirmation preview, one-time-use confirmation, dispatch reuse (11B)
  publisher/
    errors.py               PublisherError/PublisherPermanentError/PublisherRetryableError/PublisherAmbiguousError (10)
    models.py                PublisherResult/PublisherFailure/PublisherStepResult (10)
    protocol.py               Publisher: the generic advance() contract every adapter implements (10)
    registry.py                PublisherRegistry: publisher-name -> adapter resolution, no fallback (10)
    instagram/
      models.py                InstagramMediaMode/resolve_instagram_media_mode(placement) (consumes persisted placement, never re-infers from media type since 11A) (10, 11A)
      caption.py                 assemble_instagram_caption(): deterministic, mechanical-only caption assembly (10)
      validation.py               preflight checks: media reference, caption length, credentials (10)
      client.py                   MetaHttpClient/HttpxMetaHttpClient: narrow, explicit Graph API operations + read-only debug_token()/get_account_identity() (10, 11A)
      error_mapping.py             maps Meta HTTP/error-code responses to retryable/permanent/ambiguous (10)
      media_access.py              PublicationMediaAccess/S3PresignedMediaAccess: durable S3 ref -> temporary presigned URL (10)
      publisher.py                 InstagramPublisher: the checkpoint state machine (create -> poll -> publish -> reconcile) (10)
      diagnostics.py                InstagramDiagnosticsService/InstagramDiagnosticsResult: read-only /instagram_status readiness checks (11A)
  utils/
    dedup.py              in-process, non-persistent duplicate-update guard
tests/                  unit tests for the above (Telegram/AWS/Anthropic always mocked)
  conftest.py             session-wide autouse guard: blocks any real httpx.get()/httpx.post() call (11B)
docs/
  WORKFLOW.md             full Version 1 behavioral specification
  INSTAGRAM_LIVE_VALIDATION.md  operator runbook for /instagram_test_publish (11B)
  DEPLOYMENT.md                 deployment-readiness record for JustRunMy.App (11C)
Dockerfile              production image, suitable for JustRunMy.App
requirements.txt        runtime dependencies
requirements-dev.txt    runtime + test dependencies
```
