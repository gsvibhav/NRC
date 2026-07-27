# WORKFLOW.md

**Status:** Single source of truth for NRC Social Agent's Version 1 behavior.

This document is a behavioral specification only. It contains no application code and no pseudocode. It describes what the system does from the user's perspective and what internal state it holds to do it. Where it refers to components or persistence, it assumes the boundaries already defined in [../ARCHITECTURE.md](../ARCHITECTURE.md) and the scope already defined in [../PRODUCT.md](../PRODUCT.md) — it does not redefine them.

Scope: Telegram only, Version 1. No database. No publishing to any platform other than the Telegram preview itself.

---

## 1. User Journey

This section walks the full path a single piece of media takes, from the user's perspective on the left and the corresponding internal state on the right.

### 1.1 Sending media

The user sends a photo or video to the NRC Social Agent Telegram bot, in a normal chat message (not a command). This is the only way a workflow instance begins — there is no "start a post" command; sending media *is* starting a post.

Internally: Telegram delivers an update containing the message and its attached media. The bot treats this as the first event for a **new workflow instance**, scoped to that chat and that message. If a workflow instance is already active in that chat (see §1.2, interruption handling), the new media is treated as the start of a new instance once the prior one has reached a terminal state; while a prior instance is still active and awaiting the user, new unrelated media is queued behind it conceptually — the user is expected to resolve the pending post first (see §5, `/cancel`).

### 1.2 Telegram receives the update

The Telegram Adapter receives the raw update and normalizes it into an internal event: "media received, from this chat, at this time." No interpretation of the media's content happens here — that's Claude's job later. This step only confirms *that* something arrived and *what kind* of Telegram object it is (photo, video, document, etc.).

### 1.3 Media validation

Before anything is uploaded or persisted, the media is checked against Version 1's supported types (images and video, per Telegram's own media message types — not arbitrary documents) and any size constraints Telegram itself imposes on bot downloads.

- If validation **passes**, a new post record is created and the workflow proceeds to upload.
- If validation **fails**, no post record is created at all — see §6.1 for exact behavior. The user simply gets an explanatory message and remains free to send valid media.

### 1.4 Upload to Amazon S3

The validated media is transferred from Telegram to Amazon S3. This is the first point at which anything about this post exists durably. Once the upload succeeds, the post has a stable identity that survives a bot restart.

The user sees a lightweight acknowledgment (e.g. "Got it, analyzing…") — not a technical status report — once upload is confirmed.

### 1.5 Claude analysis

Claude receives the stored media and determines two things: what the post is likely about, and whether it has enough information to produce a complete, accurate caption/metadata set, or whether it needs to ask the user something first (e.g. something the media can't convey — a name, an event, an intended audience, a detail only the uploader knows).

### 1.6 Follow-up questions

If Claude determines it needs more information, it asks the user **one question at a time** (see §7, Conversation Principles) in plain chat. The user answers in a normal reply. Claude re-evaluates with the new answer in context and either asks another necessary question or proceeds to generation. This loop has no fixed number of turns — it continues until Claude has what it needs, and no longer.

### 1.7 Caption generation

Once Claude has sufficient context, it generates the full set of Version 1 output fields together, as one coherent unit:

- Caption
- Hashtags
- SEO keywords
- Category
- Alt text
- Metadata

These are generated together on the *first* pass because they are interdependent (e.g. hashtags and SEO keywords both derive from the same understanding of the subject and category). Regeneration of a *single* field later (after an edit) is handled differently — see §7 and §4 (`EDITING`).

### 1.8 Preview

The full generated post is rendered back to the user in Telegram as a single, readable preview message (or small group of messages, if media + text can't be combined in one). Below it, four actions are presented as inline buttons: **Approve**, **Edit**, **Save Draft**, **Reject**.

### 1.9 Approve / Edit / Save Draft / Reject

The user chooses exactly one action:

- **Approve** — the post is finalized. Its final content and metadata are written to S3 in their approved form. The workflow instance ends successfully. (Actual publishing to any platform is out of scope for Version 1 — approval marks the post ready, per [../PRODUCT.md](../PRODUCT.md).)
- **Edit** — the user specifies what to change. Only the affected field(s) are regenerated (or directly replaced with the user's own text, if they supply it verbatim — see §7). The rest of the post is left untouched. A new preview is shown, returning to §1.8.
- **Save Draft** — the post, in its current state (whatever has been generated so far, including any partial answers if triggered mid-flow — see §4, `SAVED_AS_DRAFT`), is written to S3 as a draft. The workflow instance ends without publication or rejection.
- **Reject** — the post is discarded. The workflow instance ends without anything being finalized. The media itself may still be retained in S3 as history (see §8) even though the post output is not.

### 1.10 Workflow completion

The workflow instance for that piece of media reaches a definitive end the moment one of Approve, Save Draft, or Reject is chosen — see §9. There is no further automatic action in Version 1 (no auto-publish, no auto-retry, no scheduled follow-up).

---

## 2. State Machine — overview

Every workflow instance (one piece of uploaded media, one conversation) occupies exactly one state at a time. States are scoped per-instance, not per-chat — though in Version 1, a chat is expected to have at most one active (non-terminal) instance at a time in practice (see §5, `/cancel`).

```
IDLE
  → RECEIVING_MEDIA
      → UPLOADING_MEDIA
          → ANALYZING_MEDIA ⇄ WAITING_FOR_USER   (loop: zero or more follow-up questions)
              → GENERATING_CONTENT
                  → SHOWING_PREVIEW
                      → EDITING → GENERATING_CONTENT → SHOWING_PREVIEW   (loop)
                      → APPROVED → COMPLETED
                      → SAVED_AS_DRAFT
                      → REJECTED

FAILED is reachable from RECEIVING_MEDIA, UPLOADING_MEDIA, ANALYZING_MEDIA,
GENERATING_CONTENT, and APPROVED (finalization failure) — see §2.13.
```

`COMPLETED`, `SAVED_AS_DRAFT`, and `REJECTED` are the only terminal states (see §9).

---

## 3. State Machine — state definitions

### 3.1 `IDLE`

- **Purpose:** No active workflow instance exists for this chat. The default resting state.
- **Entry conditions:** Chat has never sent media, or the previous workflow instance reached a terminal state (`COMPLETED`, `SAVED_AS_DRAFT`, or `REJECTED`).
- **Exit conditions:** User sends media → `RECEIVING_MEDIA`.
- **Allowed user actions:** Send media; `/start`; `/help`.
- **Failure behaviour:** Not applicable — nothing is in progress to fail.

### 3.2 `RECEIVING_MEDIA`

- **Purpose:** Confirm the incoming Telegram media is a supported type before committing any storage.
- **Entry conditions:** User sends media while in `IDLE`.
- **Exit conditions:** Supported type confirmed → `UPLOADING_MEDIA`. Unsupported type → returns to `IDLE` (see §6.1) — no post record is created, so this is not treated as entering `FAILED`.
- **Allowed user actions:** `/cancel` (no-op, since nothing durable exists yet); `/help`.
- **Failure behaviour:** Unsupported file type or size → user is told why, asked to resend; no state persists.

### 3.3 `UPLOADING_MEDIA`

- **Purpose:** Transfer the validated media into Amazon S3, establishing durable identity for this post.
- **Entry conditions:** Validation passed in `RECEIVING_MEDIA`.
- **Exit conditions:** Upload confirmed → `ANALYZING_MEDIA`. Upload fails → `FAILED`.
- **Allowed user actions:** `/cancel`; `/status`.
- **Failure behaviour:** See §6.2 (upload failures). Retried automatically a bounded number of times before moving to `FAILED`; user is only notified if it ultimately fails.

### 3.4 `ANALYZING_MEDIA`

- **Purpose:** Claude examines the media (and, on later passes, any answers gathered so far) to determine either what's still needed or that generation can proceed.
- **Entry conditions:** First entry — from `UPLOADING_MEDIA` on successful upload. Re-entry — from `WAITING_FOR_USER` once the user answers a follow-up question.
- **Exit conditions:** Claude determines more information is required → `WAITING_FOR_USER` (with a specific question queued). Claude determines it has enough → `GENERATING_CONTENT`.
- **Allowed user actions:** `/cancel`; `/status`.
- **Failure behaviour:** See §6.3 (Claude API failures). Retried automatically within limits before moving to `FAILED`.

### 3.5 `WAITING_FOR_USER`

- **Purpose:** Hold the workflow open while a specific follow-up question is pending a reply.
- **Entry conditions:** `ANALYZING_MEDIA` determined a question is necessary.
- **Exit conditions:** User replies with an answer → `ANALYZING_MEDIA` (re-evaluate). User sends `/cancel` → `REJECTED`.
- **Allowed user actions:** Reply with the answer; `/cancel`; `/status`; `/help`.
- **Failure behaviour:** No automatic timeout in Version 1 — the instance waits indefinitely for a reply. If the bot restarts while waiting, the pending question and all prior context are restored from S3 on the next interaction (see §6.6 and §8), so the user can simply answer whenever they return.

### 3.6 `GENERATING_CONTENT`

- **Purpose:** Claude produces the post's output fields (caption, hashtags, SEO keywords, category, alt text, metadata) — in full on the first pass, or for a single field on a post-edit pass (see §7).
- **Entry conditions:** `ANALYZING_MEDIA` determined sufficient context (first pass). Or: user submitted an edit from `EDITING` (partial pass).
- **Exit conditions:** Generation succeeds → `SHOWING_PREVIEW`. Generation fails → `FAILED`.
- **Allowed user actions:** `/cancel`; `/status`.
- **Failure behaviour:** See §6.3. Retried automatically within limits before moving to `FAILED`.

### 3.7 `SHOWING_PREVIEW`

- **Purpose:** Present the complete current post to the user for a decision.
- **Entry conditions:** From `GENERATING_CONTENT`, whether this is the first full generation or the result of an edit's partial regeneration.
- **Exit conditions:** Approve → `APPROVED`. Edit → `EDITING`. Save Draft → `SAVED_AS_DRAFT`. Reject → `REJECTED`.
- **Allowed user actions:** The four inline actions (Approve / Edit / Save Draft / Reject); `/status`; `/cancel` (treated as equivalent to Reject — see §5).
- **Failure behaviour:** If the preview message itself fails to send (a Telegram delivery issue, not a content problem), the send is retried; persistent failure moves to `FAILED` since the user has not yet seen anything to act on.

### 3.8 `EDITING`

- **Purpose:** Capture what the user wants changed, without disturbing the rest of the already-generated post.
- **Entry conditions:** User chose Edit from `SHOWING_PREVIEW`.
- **Exit conditions:** User specifies the field(s) to change and either supplies replacement text directly or asks Claude to regenerate that field → `GENERATING_CONTENT` (scoped to only the changed field(s)). User backs out without changing anything → returns to `SHOWING_PREVIEW` unchanged.
- **Allowed user actions:** Specify a field and new value/instruction; cancel the edit (back to preview); `/cancel` (ends the whole workflow, per §5); `/status`.
- **Failure behaviour:** Ambiguous or unrecognized edit input → the user is asked to clarify which field and what change; the instance remains in `EDITING`.

### 3.9 `SAVED_AS_DRAFT`

- **Purpose:** Preserve the post — media, all context gathered, and whatever content has been generated so far — for later, without approving or discarding it.
- **Entry conditions:** User chose Save Draft from `SHOWING_PREVIEW`.
- **Exit conditions:** None within this workflow instance — this is a terminal state (see §9). Reopening a saved draft for further work is out of scope for Version 1 (see [../PRODUCT.md](../PRODUCT.md)); the draft exists in S3 as a record, not as a resumable in-bot flow.
- **Allowed user actions:** None further in this instance; the user is free to start a new upload (`IDLE` → `RECEIVING_MEDIA`).
- **Failure behaviour:** If the write to S3 marking the post as a draft fails, the instance remains in `SHOWING_PREVIEW` and the user is told the save didn't go through, so they can retry the action rather than believing a draft exists when it doesn't.

### 3.10 `APPROVED`

- **Purpose:** Transitional state while the approved post is finalized in S3.
- **Entry conditions:** User chose Approve from `SHOWING_PREVIEW`.
- **Exit conditions:** Finalization write succeeds → `COMPLETED`. Finalization write fails → `FAILED`.
- **Allowed user actions:** None — this is a brief, system-driven transition, not a waiting point for user input.
- **Failure behaviour:** If finalization fails, the user is told approval didn't complete and is offered a retry (see §6, inline retry, not a new command) rather than being left believing the post was approved.

### 3.11 `REJECTED`

- **Purpose:** Record that the user explicitly discarded this post.
- **Entry conditions:** User chose Reject from `SHOWING_PREVIEW`, or issued `/cancel` from any non-terminal state.
- **Exit conditions:** None — terminal state (see §9).
- **Allowed user actions:** None further in this instance; user is free to start a new upload.
- **Failure behaviour:** Not applicable — rejection itself cannot meaningfully fail (it is the absence of further action, not a write that must succeed for the user's intent to be honored). If any cleanup write to S3 fails, it does not change the fact that the post is rejected from the user's perspective.

### 3.12 `COMPLETED`

- **Purpose:** Record that the post was approved and finalized successfully.
- **Entry conditions:** Finalization succeeded from `APPROVED`.
- **Exit conditions:** None — terminal state (see §9).
- **Allowed user actions:** None further in this instance; user is free to start a new upload.
- **Failure behaviour:** Not applicable.

### 3.13 `FAILED`

- **Purpose:** Capture an unrecoverable (or retry-exhausted) error at any stage, without silently losing the user's progress.
- **Entry conditions:** Any of: repeated upload failure (from `UPLOADING_MEDIA`), repeated Claude API failure (from `ANALYZING_MEDIA` or `GENERATING_CONTENT`), repeated preview delivery failure (from `SHOWING_PREVIEW`), or finalization failure (from `APPROVED`).
- **Exit conditions:** The user is shown what failed in plain language and, where the underlying data still exists (e.g. media already uploaded, answers already gathered), offered an inline **Retry** action that returns the instance to the state that failed. If the user does not retry, the instance simply remains failed and inert — the user is free to start a new upload, which begins a fresh, unrelated instance.
- **Allowed user actions:** Inline Retry (where offered); otherwise, send new media to start over; `/help`.
- **Failure behaviour:** N/A — this is itself the failure-handling state.

---

## 4. Telegram Commands (Version 1)

Only commands that change or clarify the workflow are included. Decisions and content actions (Approve/Edit/Save Draft/Reject, and failure Retry) are inline buttons, not commands, because they are choices about a specific message the user is looking at — not general-purpose commands.

| Command | Purpose |
|---|---|
| `/start` | Greets the user and explains, briefly, how to begin (send media). Only meaningful from `IDLE`; if issued mid-workflow, it's treated the same as `/help` — it does not reset or interrupt an active instance. |
| `/help` | Explains the workflow and the available actions at the user's current point in it. Available in every state. |
| `/cancel` | Ends the current active workflow instance immediately, equivalent to Reject (`REJECTED`, per §3.11). Available in every non-terminal state. A no-op in `IDLE` (nothing to cancel). |
| `/status` | Reports which state the current active instance is in, in plain language (e.g. "Waiting on your answer about the event name," or "Analyzing your media"). A no-op in `IDLE` ("No active post right now — send media to begin"). |

No `/retry` command is introduced — retry after a failure is offered as an inline action on the failure message itself (see §3.13), scoped to the specific failed instance, rather than a general command that would need to guess which instance to retry.

No draft-listing or draft-reopening command is introduced in Version 1 — drafts are write-only from the bot's perspective this version, per [../PRODUCT.md](../PRODUCT.md)'s Version 1 scope.

---

## 5. Handling interruption and `/cancel`

Because a chat is expected to have at most one active instance at a time, `/cancel` always targets *the* active instance for that chat, unresolved of which state it's in. After `/cancel` (→ `REJECTED`), the chat returns to `IDLE` and a new upload begins a wholly new instance with no memory of the cancelled one beyond whatever remains in S3 as history (see §8).

If the user sends new, unrelated media while an instance is still active and awaiting them (e.g. still in `WAITING_FOR_USER` or `SHOWING_PREVIEW`), the bot does not silently start a second concurrent instance. It reminds the user of the pending decision/question and asks them to resolve it (answer, decide, or `/cancel`) before the new media is accepted.

---

## 6. Conversation Principles

These govern how Claude behaves throughout `ANALYZING_MEDIA`, `WAITING_FOR_USER`, `GENERATING_CONTENT`, and `EDITING`:

- **Ask only necessary questions.** A follow-up question is only asked if its answer would materially change the generated caption, hashtags, SEO keywords, category, alt text, or metadata. Claude does not ask about things it can infer confidently from the media itself.
- **Never ask two questions if one is sufficient.** Each turn in `WAITING_FOR_USER` carries exactly one question. If more than one thing is genuinely ambiguous, they are asked one at a time across successive turns, not bundled — so the user is never faced with a multi-part prompt.
- **Preserve context between replies.** Every answer the user gives is retained for the rest of the instance (persisted per §8), so Claude never re-asks something already answered, and generation in `GENERATING_CONTENT` has the full accumulated context, not just the most recent reply.
- **Recover gracefully after interruption.** If the bot restarts, or the user replies much later, the instance resumes from exactly where it left off using persisted state — the user is not asked to restate anything already established, and does not need to resend the media.
- **Never regenerate everything if only one field changes.** An edit to, say, the hashtags does not trigger regeneration of the caption, alt text, or any other field. Only the field(s) the user identified in `EDITING` are regenerated (or replaced verbatim, if the user supplied exact text), and the rest of the post carries forward unchanged into the next preview.

---

## 7. Error Handling

### 7.1 Unsupported file types

Detected in `RECEIVING_MEDIA`, before any storage write. The user receives a plain explanation of what is supported (images and video) and is invited to resend. No post record is created; no state is persisted; the chat effectively remains in `IDLE`.

### 7.2 Upload failures

Detected in `UPLOADING_MEDIA` (a failure to write the already-validated media to S3). Transient failures are retried automatically a bounded number of times without involving the user. If retries are exhausted, the instance moves to `FAILED` (§3.13) and the user is told the upload didn't go through, with an inline Retry that re-attempts the upload of the same media (no need to resend it, since Telegram's copy of the file is still referenceable at this point).

### 7.3 Claude API failures

Can occur in `ANALYZING_MEDIA` or `GENERATING_CONTENT`. Transient failures (timeouts, rate limits, momentary errors) are retried automatically within limits. If retries are exhausted, the instance moves to `FAILED`, preserving everything gathered so far (media, all prior answers), and the user is offered inline Retry to re-attempt the same step — analysis or generation — without losing prior answers.

### 7.4 S3 failures

Can occur at any point where the workflow reads or writes state: upload (§7.2), persisting the follow-up Q&A history, persisting generated content, finalizing an approval, or saving a draft. The handling pattern is uniform: transient failures retry automatically; exhausted retries move the instance to `FAILED` with an inline Retry that re-attempts only the specific write that failed, since the rest of the instance's state (whatever was already durably written in earlier steps) is untouched and does not need to be redone.

### 7.5 Telegram rate limits

If the bot is rate-limited while trying to send a message (a question, a preview, a status reply, anything), the send is retried with backoff, transparently to the user — this is not treated as a workflow failure, since it is a delivery-layer condition, not a defect in the post itself. The workflow's internal state does not change while waiting out a rate limit; from the user's perspective, a reply just arrives a little later than usual.

### 7.6 Unexpected application restart

Because all workflow state is persisted to S3 as it changes (see §8), a restart does not lose an in-progress instance. On the next interaction from the user in that chat (a reply, a button press, or a new `/status`), the bot loads the persisted state for the active instance and resumes exactly where it left off — the same question if one was pending, the same preview if one was showing. The user is never asked to restart the post or resend the media because of a restart.

---

## 8. Persistence (Amazon S3 only — no database)

Per [../ARCHITECTURE.md](../ARCHITECTURE.md), all durable state lives in S3 as objects, keyed so the active instance for a given chat, and any given post's full history, can be located without a query engine. Four categories of object exist:

1. **Media.** The original uploaded file(s), written once in `UPLOADING_MEDIA` and never mutated afterward. Retained regardless of the eventual outcome (`COMPLETED`, `SAVED_AS_DRAFT`, or `REJECTED`), so history and audit remain possible even for rejected posts.

2. **Workflow state.** A record of the instance's current state (per §3), the full follow-up question-and-answer history (so context is never lost — §6), and which media it belongs to. This record is written or updated at every state transition, which is precisely what makes restart-recovery (§7.6) and indefinite waiting (§3.5) possible without a database: the record itself *is* the durable state machine position.

3. **Generated metadata.** The output fields — caption, hashtags, SEO keywords, category, alt text, and structured metadata — as produced in `GENERATING_CONTENT` and shown in `SHOWING_PREVIEW`. Updated in place when an edit regenerates a single field (§6), so the record always reflects the current preview contents, not a history of every intermediate draft of a field.

4. **Drafts.** The same shape as an in-progress workflow-state-plus-generated-metadata record, but explicitly marked as a draft when the user chooses Save Draft (`SAVED_AS_DRAFT`). A draft is a terminal snapshot in Version 1 — it is written, but not reopened by the bot (see §4).

Approved posts (`COMPLETED`) and rejected posts (`REJECTED`) are likewise terminal snapshots of the same workflow-state-plus-generated-metadata record, distinguished only by their final state value — Version 1 does not move them to a separate storage shape.

---

## 9. Completion

A workflow instance ends — and only ends — when it reaches one of exactly three states:

- **`COMPLETED`** (via `APPROVED`) — the user approved the post and finalization succeeded.
- **`REJECTED`** — the user rejected the post, or cancelled the instance outright.
- **`SAVED_AS_DRAFT`** — the user chose to preserve the post for later without approving or discarding it.

`FAILED` is not a completion state — it is a holding point that either resolves back into the normal flow via Retry, or is simply abandoned by the user in favor of starting a new upload. No workflow instance is considered "done" while still in `FAILED`.

There is no automatic publishing, scheduling, or further action after any of the three completion states in Version 1 — reaching one of them is the end of the bot's involvement with that specific piece of media until, at most, a future milestone adds publishing (see [../ROADMAP.md](../ROADMAP.md)).
