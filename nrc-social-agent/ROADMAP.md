# ROADMAP.md

Phased milestones for NRC Social Agent. Work should not pull scope forward from a later phase into an earlier one (e.g. do not build Instagram publishing while still in Phase 1).

## Phase 0 — Foundation (current)

**Goal:** Establish permanent project context before any implementation.

- [x] Create `nrc-social-agent/` as an independent project.
- [x] Document product vision and Version 1 scope (`PRODUCT.md`).
- [x] Document high-level architecture (`ARCHITECTURE.md`).
- [x] Record approved architectural decisions (`DECISIONS.md`).
- [x] Provide a placeholder-only `.env.example`.

**Exit criteria:** Foundation docs exist and are internally consistent. No code, dependencies, or credentials exist yet.

## Phase 1 — Telegram MVP

**Goal:** Deliver the full Version 1 workflow end-to-end on Telegram only.

- Telegram bot receives uploaded media.
- Media is stored in Amazon S3.
- Claude analyzes media and asks follow-up questions when needed.
- Claude generates caption, hashtags, SEO keywords, category, alt text, and metadata.
- Preview is rendered in Telegram with Approve / Edit / Save Draft / Reject.
- Each decision path is fully handled: approvals and drafts persist to S3; edits update the pending post; rejects discard or allow retry.

**Exit criteria:** A staff member can go from raw media to an approved, fully-captioned post entirely through Telegram, with state surviving a bot restart.

## Phase 2 — Hardening

**Goal:** Make the Telegram MVP reliable enough for regular internal use.

- Error handling and retries for Claude and S3 calls.
- Handling for multiple concurrent in-progress posts / multiple users.
- Basic operational visibility (logging sufficient to diagnose a failed post).
- Review of S3 key/object conventions established ad hoc in Phase 1; tighten if needed.

**Exit criteria:** The bot can be used as NRC's actual daily publishing workflow tool without hand-holding.

## Phase 3 — Instagram

**Goal:** Add Instagram as the first additional platform, proving the adapter model.

- New Platform Adapter for Instagram, reusing the existing Conversation/State Manager and Content Generation Engine unchanged.
- Instagram-specific preview/approval handling as needed (e.g. via the Telegram bot acting as the control surface, with Instagram as the publish target — exact UX to be decided at this milestone).
- Actual publishing to Instagram on Approve.

**Exit criteria:** A post approved through the existing flow can be published to Instagram without any change to the core analysis/generation pipeline.

## Phase 4 — LinkedIn

**Goal:** Add LinkedIn as a second additional platform.

- New Platform Adapter for LinkedIn, following the same pattern validated in Phase 3.

**Exit criteria:** Same as Phase 3, for LinkedIn.

## Phase 5 — Threads

**Goal:** Add Threads as a third additional platform.

- New Platform Adapter for Threads.

**Exit criteria:** Same pattern, for Threads.

## Open questions to resolve before each new-platform phase begins

- Does the new platform require reintroducing a database (e.g. for content calendars, publish scheduling)? If so, that is a deliberate decision to make at that time, not before — Version 1 through at least Phase 3 remains database-free per [DECISIONS.md](DECISIONS.md).
- Does the platform change the approval UX (e.g. approving multiple platforms' variants of the same post at once)?
