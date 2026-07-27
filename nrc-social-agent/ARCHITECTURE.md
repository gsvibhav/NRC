# ARCHITECTURE.md

This document describes the high-level system architecture only. It intentionally excludes implementation details (specific libraries, code structure, function signatures) — those belong in code and its own documentation once implementation begins.

## Guiding constraint

Every component below must be reachable through a **platform-agnostic core**. Telegram is Version 1's only bot/channel integration, but the analysis → conversation → generation → approval pipeline must not assume Telegram-specific concepts (chat IDs, inline keyboards, etc.) below the adapter boundary. Future platforms are added by writing a new adapter, not by modifying the core.

## Components

```
                         ┌─────────────────────────┐
                         │   Platform Adapters      │
                         │  (Telegram in V1;         │
                         │   Instagram/LinkedIn/      │
                         │   Threads later)           │
                         └────────────┬─────────────┘
                                      │  normalized events
                                      │  (media in, decisions in/out)
                                      ▼
                         ┌─────────────────────────┐
                         │   Conversation / State    │
                         │        Manager             │
                         │ (tracks in-progress posts, │
                         │  follow-up Q&A, pending    │
                         │  approvals)                │
                         └────────────┬─────────────┘
                                      │
                     ┌────────────────┼─────────────────┐
                     ▼                                  ▼
        ┌───────────────────────┐         ┌───────────────────────────┐
        │  Media Ingestion &     │         │  Claude Analysis &         │
        │  S3 Storage Layer      │◄───────►│  Content Generation Engine │
        │ (raw media, drafts,    │         │ (media analysis, follow-up │
        │  approved posts,       │         │  questions, caption/        │
        │  metadata as objects)  │         │  hashtags/SEO/category/    │
        │                        │         │  alt text/metadata)        │
        └───────────────────────┘         └───────────────────────────┘
                     ▲
                     │  persisted post records
                     ▼
        ┌───────────────────────────────────────────┐
        │   Preview & Approval Flow                   │
        │ (renders generated post back to the         │
        │  originating adapter; routes Approve/Edit/   │
        │  Save Draft/Reject back through the           │
        │  Conversation/State Manager)                  │
        └───────────────────────────────────────────┘
```

### Platform Adapters

Translate a specific platform's inbound events (media upload, button press, text reply) into a normalized internal event, and normalized internal output (a preview, a question, a confirmation) back into that platform's UI primitives.

- **Telegram Adapter (V1):** receives uploaded media and text replies via the Telegram Bot API; renders previews and Approve/Edit/Save Draft/Reject as Telegram inline actions.
- **Future adapters (Instagram, LinkedIn, Threads):** same contract — receive media/instructions from that platform's API, render previews/decisions in that platform's idiom. Publishing to the platform (once in scope) is also adapter-owned, since publish mechanics are platform-specific.

### Conversation / State Manager

Owns the lifecycle of a single "post in progress": which media it's for, what questions have been asked and answered, what generated content exists so far, and what decision (if any) has been made. This is the one place that understands the Version 1 workflow (upload → analyze → question → generate → preview → decide) independent of which platform triggered it.

### Media Ingestion & S3 Storage Layer

Responsible for getting uploaded media into S3 and for all persistence in the system — there is no database in Version 1. This layer stores:

- Raw uploaded media, keyed by a stable identifier.
- In-progress conversation/post state (so a post survives a bot restart).
- Draft posts (Save Draft).
- Approved posts and their full generated metadata.
- Rejected posts, if retained for audit/history.

See "Persistence model" below for the shape this takes without a database.

### Claude Analysis & Content Generation Engine

Wraps all calls to the Claude API. Two responsibilities that are related but distinct:

1. **Analysis** — given media (and any answered follow-up questions), determine what the post is about and what, if anything, is still ambiguous enough to need a follow-up question.
2. **Generation** — produce the final structured output: caption, hashtags, SEO keywords, category, alt text, and metadata.

This engine is platform-agnostic: it takes media + conversation context in, and returns either "ask this question" or "here is the generated post" — it has no knowledge of Telegram or any other adapter.

### Preview & Approval Flow

Takes a generated post from the Content Generation Engine and hands it to the Conversation/State Manager to be rendered by the originating adapter as a preview with the four decision actions. Routes the resulting decision (Approve / Edit / Save Draft / Reject) back to state management and, on Approve, marks the post as finalized in S3. Actual publishing to the destination platform is out of scope for the Telegram-only Version 1 and becomes an adapter responsibility in later milestones (starting with Instagram — see [ROADMAP.md](ROADMAP.md)).

## Persistence model (no database, V1)

All state lives in Amazon S3 as objects. At a high level, the storage layer separates:

- **Media objects** — the raw uploaded files.
- **Post/state records** — structured data (conversation state, generated fields, decision status) associated with a given media upload.

Because there is no database, lookups (e.g. "what's the pending post for this chat") rely on a predictable key scheme rather than queries. The exact bucket/key naming convention is an implementation detail to be defined when building the storage layer, not a Version 1 architectural constraint — but it must support: finding the in-progress post for a given conversation, listing drafts, and retrieving an approved post's full metadata.

## Extensibility for future platforms

Adding Instagram, LinkedIn, or Threads (see [ROADMAP.md](ROADMAP.md)) should require:

1. A new Platform Adapter implementing the same normalized event contract.
2. Optionally, platform-specific fields in the generated metadata (e.g. Instagram-specific hashtag limits) — handled as adapter-level formatting of the same underlying generated content, not a new generation pipeline.
3. Optionally, publish capability in that adapter, once publishing (not just preview) is in scope for that platform.

No future platform should require changes to the Conversation/State Manager or the Claude Analysis & Content Generation Engine's core contract.

## Hosting & deployment (high-level)

- **Hosting:** JustRunMy.App runs the deployed bot/service.
- **Deployment:** GitHub is the source of truth; deployment to JustRunMy.App is driven from this repository.
- **Configuration:** Telegram, AWS, and JustRunMy credentials are supplied via environment variables (see `.env.example`) — never committed to the repository.

Configuring these services (Telegram bot token, AWS credentials, JustRunMy deployment) is explicitly deferred to a later milestone and is not part of this foundation phase.
