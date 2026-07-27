# Controlled Instagram Live Validation — Operator Runbook

This is an **operational document**, not a foundation planning document
(see [CLAUDE.md](../CLAUDE.md), [PRODUCT.md](../PRODUCT.md),
[ARCHITECTURE.md](../ARCHITECTURE.md), [ROADMAP.md](../ROADMAP.md),
[DECISIONS.md](../DECISIONS.md) for those). It's the step-by-step guide an
operator follows to run the first — and any subsequent — supervised,
controlled Instagram live publication through `/instagram_test_publish`.

See [README.md](../README.md)'s "Controlled Instagram Live Validation"
section for the architecture and safety design this runbook operates.

## Preconditions

Before running anything:

- A **professional Instagram test account** (Business or Creator) exists
  and is not the organization's real production account, unless a real
  publication is explicitly intended.
- The correct Meta app is configured for Instagram API with Instagram
  Login (Business Login) — see README.md's "Selected authentication
  model".
- `INSTAGRAM_ACCOUNT_ID` and `META_ACCESS_TOKEN` are configured through
  the hosting platform's secret mechanism (never pasted into a chat,
  never committed to a file).
- `INSTAGRAM_PUBLISHING_ENABLED=true`.
- `INSTAGRAM_LIVE_TEST_OPERATOR_IDS` includes your own Telegram user ID.
- The Meta app's access/review status has been **manually confirmed** —
  this system does not and cannot verify App Review/Advanced Access
  status on your behalf; a token can be structurally valid and still be
  rejected by Meta for review-status reasons this tool has no visibility
  into.
- An approved publication exists whose media is a **single JPEG image**
  destined for the Instagram **Feed** (not a Reel — Reels are out of
  scope for this milestone, see README.md's "Known limitations").
- The media's underlying S3 object is reachable (no other process holds
  it, no pending deletion).
- No unresolved prior execution exists for the account you're about to
  test against — check with `/status` first.

## Step 1 — Read-only verification

1. Run `/instagram_status`.
2. Confirm `Token: Valid`.
3. Confirm `Permissions: Verified`.
4. Confirm `Account: Verified (@your_test_account)` — check the username
   is the account you expect, not a different one.
5. Confirm `Status: Ready to publish`.

If any line is anything other than the above, stop here and resolve it
(see "Failure procedures" below) before proceeding — `/instagram_test_publish`
itself re-checks this, but resolving it here first avoids wasted preview
cycles.

## Step 2 — Controlled live validation

1. Run `/instagram_test_publish`.
2. If told no eligible publication exists, prepare one first (upload,
   approve, and let publication preparation + execution creation
   complete normally — see README.md's earlier milestones) — it must
   resolve to a single approved JPEG destined for Feed.
3. Review the confirmation screen carefully:
   - **Account** — is this the account you intend to publish to?
   - **Account ID** — does the masked suffix match what you expect?
   - **Placement** — must say "Instagram Feed."
   - **Media** — must say "Single JPEG image."
   - **Approved version** — is this the exact draft version you reviewed
     and approved?
4. If anything looks wrong, tap **Cancel** — nothing is published, and
   you can start over with `/instagram_test_publish` once you've
   resolved the discrepancy.
5. If everything is correct, tap **Confirm live publication**. This
   creates one real, public Instagram post. It cannot be automatically
   undone.
6. Observe the dispatch messages as they arrive.
7. Once you see a completion message, run `/status` to independently
   confirm the durable execution state (never rely on the Telegram
   message alone — see "Completed but Telegram notification failed"
   below).
8. **Verify the actual post manually on Instagram** — open the app or
   website and confirm the post exists, looks correct, and matches the
   approved content.
9. Record the permalink (if shown) and the time you verified it, for
   your own operational record.

## Failure procedures

- **Invalid or expired token** — `/instagram_status` reports `Token:
  Invalid`. Refresh/rotate the token through the hosting platform's
  secret mechanism (see "Credential handling" below), restart the
  application, re-run `/instagram_status`.
- **Missing permission** — `/instagram_status` reports `Permissions:
  Missing required permissions`. The token was not granted
  `instagram_business_basic`/`instagram_business_content_publish` —
  re-authorize the app with the correct scopes.
- **Account mismatch** — the confirmation screen's `@username` isn't the
  account you expected. Tap **Cancel** immediately; do not confirm.
  Check `INSTAGRAM_ACCOUNT_ID`.
- **Media fetch failure / container rejected** — the confirmation
  preview appeared but the dispatch itself failed before publishing
  (`{status}: retryable_failure` or `permanent_failure` with no
  publication). Nothing was published. Check the safe failure message;
  `/retry` is available for a retryable failure and reuses the existing
  confirmation's authorization — no new confirmation is required.
- **Processing timeout** — Instagram took longer than the configured
  polling window to process the container. Reported as a retryable
  failure; `/retry` resumes polling from where it left off.
- **Rate limit** — reported as a retryable failure; wait before
  retrying.
- **Permission revoked mid-flow** — reported as a permanent failure; the
  underlying publication package is untouched and safe.
- **Ambiguous publish result** — see the dedicated section immediately
  below. This is the one outcome that requires manual judgment rather
  than a mechanical retry.
- **Completed execution with failed Telegram notification** — see
  "Completed but Telegram notification failed" below.

## Ambiguous outcome procedure

If the bot reports:

> Meta may have accepted the publication, but the final result could not
> be confirmed.

**Do not retry. Do not create another publication for this content.**

1. Open Instagram and manually check the target account's recent posts.
2. Compare the expected publication time, content, and account against
   what you actually see.
3. Record your finding (published / not published / unclear) alongside
   the execution's `publication_id`.
4. Do not attempt to "fix" this by re-running `/instagram_test_publish`
   or `/retry` — both are structurally prevented from blindly retrying
   an execution in this state (see README.md's "Ambiguous outcome
   recovery"), and forcing it is never the right response regardless.
5. If genuinely unresolved, escalate for manual reconciliation — this is
   a documented, accepted limitation of Meta's Content Publishing API
   (no idempotency key exists for the publish call), not a bug in this
   system.

## Cleanup

If a test post needs to be removed, **delete it manually through
Instagram** (the app or website). This system never attempts to delete
a post automatically — there is no delete capability anywhere in this
codebase, by design.

## Credential handling

- Configure `INSTAGRAM_ACCOUNT_ID`/`META_ACCESS_TOKEN`/
  `INSTAGRAM_LIVE_TEST_OPERATOR_IDS` through the hosting platform's own
  secret/environment-variable mechanism — never paste a real token into
  a Telegram message, a chat with an AI assistant, or any file that
  might be committed.
- Never commit a real `.env` file. `.env.example` contains placeholders
  only.
- After rotating or updating any credential, restart the application —
  configuration is read once, at startup.
- After a restart, always re-run `/instagram_status` before attempting
  another live validation, to confirm the new credential is actually
  valid and configured correctly.
