# PRODUCT.md

## Vision

NRC Social Agent is a private AI-powered publishing assistant that turns the act of "posting to social media" into a conversation. A staff member uploads media to a chat, the assistant analyzes it with Claude, asks whatever follow-up questions it needs, and produces a complete, ready-to-review post — caption, hashtags, SEO keywords, category, alt text, and metadata — for approval before anything goes out.

The goal is to remove the repetitive, low-value work of writing captions and metadata by hand for every piece of media, while keeping a human as the final approver for everything that gets published.

## Who this is for

Internal NRC staff responsible for social-media publishing. This is not a public-facing product and not a multi-tenant SaaS — it is a private tool operated by and for NRC.

## Why Telegram first

Telegram is the first platform because:

- Its Bot API natively supports rich media upload, inline buttons (for Approve / Edit / Save Draft / Reject), and conversational follow-up — everything Version 1 needs — without requiring a custom UI.
- It lets the team validate the core AI workflow (analysis → questions → generation → approval) before investing in platform-specific publishing integrations (e.g. Instagram's Graph API, LinkedIn's API).
- It decouples "where does the assistant get instructed" from "where does content ultimately get published" — a distinction the architecture preserves for every future platform.

## Version 1 scope

**In scope:**

1. User uploads media (photo/video) to the NRC Social Agent Telegram bot.
2. Media is stored in Amazon S3.
3. Claude analyzes the media.
4. Claude asks the user follow-up questions where needed (e.g. context Claude can't infer from the media alone).
5. Claude generates, for the post:
   - Caption
   - Hashtags
   - SEO keywords
   - Category
   - Alt text
   - Metadata (structured, for future reuse — e.g. by future platform adapters)
6. A preview of the generated post is shown back in Telegram.
7. The user takes one of four actions on the preview:
   - **Approve** — post is finalized and marked ready.
   - **Edit** — user revises fields; assistant regenerates/updates as needed.
   - **Save Draft** — post is stored in S3 for later completion, not published.
   - **Reject** — post is discarded (or the assistant retries analysis/generation, per Version 1 refinement).

**Explicitly out of scope for Version 1:**

- Actually publishing to Instagram, LinkedIn, or Threads — Version 1 produces content and a decision (approve/edit/draft/reject), it does not push to any platform other than showing the preview back in Telegram.
- Any database — all persistence is in Amazon S3.
- Multi-tenant support, public sign-up, or self-service onboarding.
- Scheduling/queuing of posts for future publish times.
- Analytics or performance tracking of published content.

## Future platforms

Instagram, LinkedIn, and Threads are planned as subsequent milestones (see [ROADMAP.md](ROADMAP.md)). Instagram publishing is explicitly called out as the next milestone after the Telegram MVP is solid. The architecture (see [ARCHITECTURE.md](ARCHITECTURE.md)) is required to support adding these without a redesign — each platform should slot in as a new adapter around the same analysis/generation/approval core, not a fork of it.

## Success criteria for Version 1

- A staff member can go from "media in hand" to "approved, fully-captioned post" entirely through a Telegram conversation, with no other tool involved.
- Every generated field (caption, hashtags, SEO keywords, category, alt text, metadata) is present and reviewable before approval — nothing is auto-published without an explicit Approve.
- Drafts and rejected/approved posts persist reliably in S3 across bot restarts.
