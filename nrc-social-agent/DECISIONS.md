# DECISIONS.md

Architectural decisions already approved for NRC Social Agent. Each entry is permanent context — if a decision is later revisited, add a new entry rather than editing history, and mark the old one superseded.

---

## 1. Project independence from `nrc-ai-agents`

**Decision:** `nrc-social-agent/` is a fully independent project within this repository. It does not depend on `nrc-ai-agents/` code, skills, or configuration.

**Status:** Approved.

**Rationale:** NRC Social Agent is a distinct product (a social publishing assistant) with its own lifecycle, stack, and deployment target. Coupling it to `nrc-ai-agents/` would make either project harder to change independently and risks pulling in unrelated tooling (e.g. design skills) that has no bearing on this product.

---

## 2. Language: Python

**Decision:** NRC Social Agent's implementation language is **Python**.

**Status:** Approved.

**Rationale:**

- **Telegram Bot API maturity:** Python has multiple mature, actively maintained async-first libraries for the Telegram Bot API (e.g. `python-telegram-bot`, `aiogram`) that directly support the media upload + inline-keyboard (Approve/Edit/Save Draft/Reject) interactions Version 1 needs.
- **Claude API parity:** Anthropic's Python SDK is first-class and well-documented, with no functional gap against the Node.js/TypeScript SDK for this use case (multimodal analysis, multi-turn conversation, structured generation).
- **AWS S3 integration:** `boto3` is the reference AWS SDK and is arguably the most mature and widely-documented S3 client of any language, which matters here since S3 is not just storage but the *entire* persistence layer (no database).
- **Conversational state machines:** The core workflow (upload → analyze → ask follow-up → generate → preview → decide) is naturally modeled as an async state machine per conversation. Python's asyncio ecosystem, combined with the Telegram libraries above, handles this cleanly without extra framework weight.
- **Media handling headroom:** Version 1 only needs to pass media through to Claude and S3, but future milestones (Instagram, LinkedIn, Threads) will likely need image/video preprocessing (resizing, format conversion, thumbnailing) for platform-specific requirements. Python's media-handling ecosystem (Pillow, etc.) is deep and well-trodden for this.

Node.js was considered — it is equally capable of talking to Telegram, Claude, and S3, and would share a language with the vanilla-JS `NRC Website` project. It was not chosen because (a) this project is explicitly independent and gains no real benefit from language-sharing with the website, and (b) it offers no concrete advantage over Python for this specific workload, while Python's async Telegram libraries and media ecosystem are a better match for where the roadmap is headed (Phases 3–6).

---

## 3. No database in Version 1

**Decision:** All persistence — media, conversation/post state, drafts, approved posts — lives in Amazon S3. No relational, document, or key-value database is introduced in Version 1.

**Status:** Approved.

**Rationale:** Version 1's persistence needs are simple (store media, store per-conversation post state, list drafts) and don't require query capabilities beyond key-based lookup. Introducing a database adds operational surface (provisioning, migrations, another credential set) with no Version 1 benefit. This can be revisited explicitly in a future milestone (see [ROADMAP.md](ROADMAP.md)) if a platform's requirements (e.g. scheduling, content calendars) demand querying that S3 can't reasonably provide.

---

## 4. Hosting: JustRunMy.App

**Decision:** The service is hosted on JustRunMy.App.

**Status:** Approved.

---

## 5. Bot integration: Telegram Bot API (Version 1)

**Decision:** Version 1's sole platform integration is the Telegram Bot API, used both as the intake channel (media upload) and the control surface (follow-up questions, preview, Approve/Edit/Save Draft/Reject).

**Status:** Approved.

---

## 6. AI: Claude API

**Decision:** All media analysis, follow-up question generation, and content generation (caption, hashtags, SEO keywords, category, alt text, metadata) is performed via the Claude API.

**Status:** Approved.

---

## 7. Deployment: GitHub

**Decision:** This repository is the source of truth; deployment to JustRunMy.App is driven from GitHub.

**Status:** Approved.

---

## 8. Platform-agnostic core for future extensibility

**Decision:** The analysis/generation/approval core must not encode Telegram-specific (or any single platform's) concepts. Future platforms (Instagram, LinkedIn, Threads) are added as adapters around this core, per [ARCHITECTURE.md](ARCHITECTURE.md).

**Status:** Approved.

**Rationale:** Explicitly required so that adding future platforms does not require redesigning the application, per the product's core architectural constraint.

---

## 9. Publication package immutability, and the Package/Execution split

**Decision:** A `PublicationPackage` (`src/publication/`), once `READY_FOR_PUBLISHING`, is immutable forever — it is never rewritten to reflect anything about a later publishing *attempt* (status, platform response, platform post ID, retry count). All runtime publishing state belongs exclusively to a separate `PublicationExecution` record (`src/execution/`), keyed off the package's own `publication_id`. The package answers "what should be published"; the execution record answers "what has been attempted, and what happened." Neither may be merged into the other, and execution must never write back onto the package.

**Status:** Approved.

**Rationale:** These two concerns evolve at very different rates and for different reasons — package content is decided once, at approval time, from an immutable draft version; execution state changes with every dispatch attempt, retry, and eventual platform response. Conflating them would mean either re-validating/re-freezing already-approved content on every publish attempt, or letting publish-attempt bookkeeping quietly erode the "immutable content contract" the publication-preparation layer (Milestone 8) was built specifically to guarantee. Keeping them as separate S3 objects (`publications/<workflow_id>/<output_id>.json` vs. `executions/<publication_id>.json`) makes the immutability guarantee structural (there is no code path that writes execution-domain fields into the package's own schema) rather than merely a convention that later code could accidentally violate. This also cleanly anticipates a future publisher adapter's own needs: it consumes an immutable `PublicationPackage` plus its own `PublicationExecution` runtime record, never needing to reconcile "current content" against "attempt history" inside one mutable object.

---

## 10. Instagram Publisher Adapter: authentication model, media delivery, explicit authorization, and at-most-once dispatch

**Decision:** The first real external platform integration (`src/publisher/instagram/`) is built on four specific choices, each with residual implications worth recording permanently rather than only in code comments:

1. **Authentication model:** Instagram API with Instagram Login (Business Login), not Facebook Login for Business — requires only a standalone Instagram Business/Creator account (`instagram_business_basic` + `instagram_business_content_publish`), no linked Facebook Page. A long-lived Instagram User access token is supplied via `META_ACCESS_TOKEN`; no in-app OAuth flow is implemented (out of scope for this milestone).
2. **Media delivery:** durable S3 references are converted to temporary, narrowly-scoped presigned GET URLs (`boto3.generate_presigned_url`) on demand for each dispatch attempt — never persisted, never logged in full, never widening the bucket's own access policy.
3. **Explicit publish authorization is required before any Meta call** — approval alone does not authorize publishing. A dedicated "Publish to Instagram" Telegram action persists `dispatch_authorization` (who, when, which exact execution/publication) before the first side effect; `/retry` can resume an already-authorized dispatch but can never create the first authorization.
4. **At-most-once, not exactly-once, is the achievable and claimed guarantee.** Meta's Content Publishing API provides no reliable idempotency key for the one irreversible call (`media_publish`). This system guarantees no *duplicate* publish through a durable claim/lease (OCC), a checkpoint persisted before every irreversible call, and conservative reconciliation of ambiguous outcomes (a single read-only container-status check; if that can't produce positive evidence, the execution is marked a permanent, non-retryable failure requiring operator intervention — never a second blind attempt).

**Status:** Approved.

**Rationale:** Each choice trades some completeness for safety, deliberately: Instagram Login avoids a Facebook Page dependency this private, single-account tool doesn't need. Presigned URLs are the narrowest AWS-native mechanism for temporary external media access without a public bucket. Explicit authorization exists because this milestone introduces the system's first genuinely irreversible, publicly-visible side effect — the brief's own repeated emphasis, and this codebase's consistent "never guess at a user's intent" precedent, both argue against inferring publish consent from approval alone. The at-most-once (not exactly-once) framing exists because claiming a stronger guarantee than the underlying platform API can support would be dishonest — Meta's own documented container-status semantics (`PUBLISHED` as a real, queryable terminal state) provide the *only* reliable reconciliation signal available, and this system uses exactly that signal and no more, defaulting to safe inaction (operator intervention) whenever it's insufficient.

---

## 11. Explicit placement contract, resolved once and persisted (Milestone 11A)

**Decision:** `output_type` (e.g. `instagram_reel_caption`) is never treated as a reliable description of actual Instagram placement. Instead, `src/publication/models.py::resolve_placement(channel, media_type)` — a deterministic, code-owned mapping from the approved media's own MIME type — is called exactly once, at publication-package build time (`src/publication/service.py`), and its result is persisted on the package itself (`PublicationPackage.placement`, publication schema v2). Every downstream consumer, starting with `InstagramPublisher`, retrieves it via `PublicationPackage.resolved_placement()` and never re-derives it independently. This was chosen over the alternative considered during Milestone 11's planning phase — splitting `output_type` itself into separate Feed/Reel variants — because it requires zero changes to Milestones 5/6's already-shipped content-planning/draft-generation schemas and prompts, while still correctly recording the true Meta placement for the publisher, previews, and any future auditing/analytics reader. A historical (schema v1) package with no persisted `placement` is never rewritten to add one — `resolved_placement()` derives the same answer in-memory, from that package's own already-persisted `channel`/`media[0].media_type`, preserving immutability exactly (no S3 write is ever made to a package after its initial creation, for any reason, including this one).

**Status:** Approved.

**Rationale:** Resolving placement once and persisting it, rather than letting each consumer re-infer it from raw media type, eliminates an entire class of future drift risk: if a second consumer (a preview renderer, an analytics job) ever needed to know placement, an inference-only design would require it to reimplement (and keep in sync with) the exact same MIME-type mapping the publisher uses — any divergence between the two would be a silent, hard-to-detect bug. Persisting the resolution once, at the one point the approved media is already known and validated, makes "what placement will this become" a fact recorded in the domain's own data model rather than a computation repeated ad hoc by every future reader.

---

## 12. Controlled live-validation confirmation state lives in the execution domain, not a new one (Milestone 11B)

**Decision:** The durable, operator-confirmed "about to attempt a real Instagram publication" state that `/instagram_test_publish` requires is persisted as `metadata["live_validation"]` directly on the target `ExecutionDocument` (`executions/<publication_id>.json`), written through a new, narrow, OCC-protected `ExecutionManager.set_live_validation_state()` — reusing the execution domain's *existing* etag/conditional-write machinery. No new S3 namespace, store, repository, or manager was introduced for this. Consuming a confirmation (`AWAITING_CONFIRMATION` → `CONFIRMED`) is itself one more OCC-protected write using the exact etag loaded at the start of `confirm()`; whichever of two concurrent confirm attempts loses that race is rejected before it can ever call `ExecutionDispatchService.authorize_and_dispatch()`.

**Status:** Approved.

**Rationale:** The confirmation's entire purpose is answering "is it still safe to dispatch *this specific execution*, right now" — that is exactly the execution domain's own existing concern (an `ExecutionDocument` already tracks attempt count, dispatch status, checkpoint, lease, and authorization for precisely this reason), not a separate concept needing its own persistence lifecycle. Reusing the execution domain's OCC/etag machinery gives one-time-use consumption "for free," using a concurrency primitive already proven correct by Milestone 10's own claim/lease/checkpoint design, rather than inventing and independently verifying a second concurrency mechanism for what is fundamentally the same kind of guarantee (never let two racing writers both believe they won). The milestone's own brief explicitly asked to prefer extending existing execution authorization state over a wholly separate persistence domain unless one was genuinely necessary — it was not.

---

## 13. Deployment mechanism: GitHub Actions Git-push to JustRunMy.App, non-forced

**Decision:** `.github/workflows/deploy-social-agent.yml` (repository root) is this project's sole automated deployment path, realizing entry 7's ("deployment to JustRunMy.App is driven from GitHub") previously unspecified mechanism. On a real run it pushes `HEAD` (this repository's own commit history, not a squashed/synthetic one) to a short-lived Git remote pointed at JustRunMy.App's Git-based deploy endpoint, targeting a `deploy` branch, using a plain (never forced) push. The remote is added and removed within the same workflow step; credentials are supplied only via GitHub repository secrets, interpolated directly into the one `run:` block that needs them, with shell tracing never enabled around that step. The workflow's own Docker build is CI validation only (confirming the image still builds) — it never pushes an image anywhere; JustRunMy.App performs its own build from the Git push. The `push` trigger is `paths`-filtered to `nrc-social-agent/**` (plus the workflow file itself), so unrelated changes elsewhere in this monorepo never trigger a social-agent deployment.

**Status:** Superseded by entry 14 — pushing `HEAD` directly deploys the entire monorepo (this repository's three independent projects sharing one `main` branch), not just `nrc-social-agent/`. The non-forced-push and `paths`-filtering rationale below both still hold and carry forward unchanged into entry 14.

**Rationale:** A plain, non-forced push was chosen deliberately over a reflexive `--force`: this repository cannot verify JustRunMy.App's actual Git-deploy semantics (whether a force push is expected/safe for their model) without platform-specific documentation this milestone doesn't have access to, and a forced push is destructive if that assumption is wrong — consistent with this project's established pattern (see Milestone 11C's health-check question) of flagging a genuine unknown for the operator to confirm rather than guessing at platform behavior with an irreversible action. `paths`-filtering was added even though the NRC Website's own `deploy-production.yml` doesn't have one, because that workflow's single-app repo doesn't share a `main` branch with unrelated projects the way this monorepo does — deploying nrc-social-agent on every push to `main`, including pushes that only touched "NRC Website/" or "nrc-ai-agents/", would be pure waste (a redundant Docker build and Git push of unchanged code) with no corresponding benefit, so this is a justified, monorepo-specific adaptation, not a stylistic deviation from the shared deployment philosophy.

---

## 14. Subtree-only deployment via `git subtree split`, single pre-built deploy-URL secret

**Decision:** `.github/workflows/deploy-social-agent.yml` deploys **only** `nrc-social-agent/`'s own content to JustRunMy.App, never the surrounding monorepo. It does this with Git's native `git subtree split --prefix=nrc-social-agent`, generating a synthetic commit whose tree is exactly this directory's contents at the root (never nested, never alongside "NRC Website/"/"nrc-ai-agents/") — that commit, not `HEAD`, is what's pushed to JustRunMy.App's `deploy` branch (via a fully-qualified `refs/heads/deploy` destination, not a bare `deploy`, which git rejects for a raw-commit-SHA source). This is not a second, separately-maintained deployment repository and never rewrites this repository's own history — the split commit exists only for the duration of one workflow run. Deployment credentials moved from three separate secrets concatenated into a URL by the workflow to a single `JUSTRUNMYAPP_GIT_URL` secret holding the complete, pre-built URL.

**Status:** Approved. Supersedes entry 13's `HEAD`-push mechanism; carries forward its non-forced-push and `paths`-filtering decisions unchanged.

**Rationale:** `git subtree split` was chosen over alternatives (a separate mirrored deployment repository kept in sync some other way, or a build step that copies `nrc-social-agent/` into a fresh directory and force-pushes it with synthetic history) because it's Git's own native mechanism for exactly this problem, requires no second repository to maintain, and — critically, verified directly in a sandbox before this decision was finalized, not assumed — produces a **deterministic** commit SHA from the same source history and prefix, even from a completely fresh, independent clone with no local cache (exactly how every GitHub Actions run starts). That determinism is what lets consecutive real deployments fast-forward against each other safely without `--force`, which a naive "extract and force-push synthetic history" approach could not offer as a similarly verified guarantee. The single-URL-secret redesign was chosen over three separate username/password/repo secrets because concatenating credentials into a URL inside the workflow would require the workflow itself to URL-encode a password to stay safe against a special character like `@` or `:` — a single pre-built secret removes that entire risk class, since nothing in the workflow parses or reconstructs the URL. This decision also formally records a genuine prerequisite gap discovered while implementing it: as of this entry, `nrc-social-agent/` has no commit history in this monorepo at all, so `git subtree split` cannot yet produce a real result on a real run — this is a repository-state fact to resolve through ordinary commits, not something this decision or the workflow itself can or should work around.
