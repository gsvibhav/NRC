# Deployment Readiness — Operator Reference

This is an **operational document**, not a foundation planning document
(see [CLAUDE.md](../CLAUDE.md), [ARCHITECTURE.md](../ARCHITECTURE.md),
[DECISIONS.md](../DECISIONS.md) for those). It records exactly what this
milestone verified about deploying NRC Social Agent to JustRunMy.App (or
any container-based host), and — just as importantly — what it could
**not** verify without real credentials or platform-specific access.

No real Meta or Telegram API call, and no real deployment, was made while
producing this document. Every claim below was verified either by
reading the actual code or by running the built Docker image locally
with fake/placeholder configuration.

## What "deployable" means for this service

A single, stateless container running one long-polling process
(`python -m src.main`). No HTTP server, no local persistent storage
beyond ephemeral downloads to `/tmp` (cleaned up after use), no database.
All durable state lives in S3; all configuration comes from environment
variables, read exactly once at startup (`Config.from_env()`).

## Verified this milestone

- **Docker image builds cleanly** from a clean checkout (`docker build .`).
- **Runs as a non-root user** (`appuser`, uid 1000) — confirmed the
  process actually runs as uid 1000, and that this user can write to
  `/tmp` (needed for `src/media/download.py`'s `tempfile.mkstemp()` —
  the only local disk write anywhere in this codebase).
- **No secrets reach the image**: `.dockerignore` excludes `.env`/`.env.*`
  (keeping only `.env.example`), and the `Dockerfile` only ever `COPY`s
  `src/` — no root-level files, no test fixtures, no `.env` of any kind.
- **`requirements.txt` is fully pinned** (5 production dependencies,
  every one an exact `==` version) and the Dockerfile installs only
  `requirements.txt` — `requirements-dev.txt` (which layers `pytest`/
  `pytest-asyncio` on top via `-r requirements.txt`) never reaches the
  production image.
- **`config.py` and `.env.example` are in exact 1:1 parity** — every
  environment variable name `Config.from_env()` reads has a matching
  documented entry in `.env.example`, and vice versa (verified by
  extracting both sets programmatically and diffing them; zero drift).
- **Fail-fast startup verified directly against the built image**, not
  just unit tests — each of the following produces a clear stderr
  message and **exit code 1**, exactly what a container orchestrator
  needs to detect and report a failed deployment:
  - No environment variables at all → fails on the first required one
    (`TELEGRAM_BOT_TOKEN`).
  - All variables set except `S3_BUCKET_NAME` → fails with that specific
    variable named.
  - `INSTAGRAM_PUBLISHING_ENABLED=true` without
    `INSTAGRAM_ACCOUNT_ID`/`META_ACCESS_TOKEN` → fails with the specific
    missing variable named.
- **Successful startup verified directly against the built image**, with
  fake-but-well-formed configuration, in two configurations:
  - `INSTAGRAM_PUBLISHING_ENABLED=false` and no Instagram variables set
    at all — starts cleanly; `live_validation_service`/
    `instagram_diagnostics_service` are still constructed and report a
    clean "disabled" state with zero Instagram configuration present.
  - `INSTAGRAM_PUBLISHING_ENABLED=true` with placeholder
    `INSTAGRAM_ACCOUNT_ID`/`META_ACCESS_TOKEN`/
    `INSTAGRAM_LIVE_TEST_OPERATOR_IDS` — starts cleanly; operator-list
    membership resolves correctly.
- **Logging goes to stdout only** (`src/logging_config.py`, `stream=
  sys.stdout`) — no local file logging, no log rotation to configure,
  compatible with any container platform's log capture.
- **Graceful shutdown**: `application.run_polling()` is called with no
  overridden `stop_signals`, so `python-telegram-bot`'s own default
  `SIGINT`/`SIGTERM`/`SIGABRT` handling applies — an orchestrator's
  ordinary "send SIGTERM, wait, then SIGKILL" shutdown sequence is
  handled by the library, not something this codebase needed to
  implement itself.
- **S3 IAM policy documentation corrected** — README.md's "Required S3
  setup" was stale: it was missing `drafts/*`, `publications/*`, and
  `executions/*` entirely (added in Milestones 6/7, 8, and 9
  respectively) and incorrectly still said `drafts/*` "doesn't need
  permissions yet." A deployment following the old documentation
  literally would have failed with `AccessDenied` on the very first
  draft/approval/execution write. Corrected to list all eight prefixes
  this codebase actually uses, verified directly against every
  `_KEY_PREFIX`/`S3_KEY_PREFIX` constant in `src/`, not assumed.

## What this milestone could not verify (and did not fabricate)

Object/client construction never itself makes a network call in this
codebase — `Application.builder().token(x).build()` (Telegram) and
`boto3.client("s3", ...)` (AWS) are both purely local; the underlying
network call only happens on first real use (`run_polling()`,
respectively the first S3 request). This means:

- **Real Telegram connectivity** (a real bot token actually reaching
  `api.telegram.org`) cannot be verified without a real token — exactly
  parallel to the Meta/Instagram situation Milestone 11 already
  documented. Not tested here; requires the operator's real
  `TELEGRAM_BOT_TOKEN`.
- **Real S3 connectivity** (a real IAM identity actually reaching the
  configured bucket) cannot be verified without real AWS credentials and
  a real bucket. Not tested here.
- **Real Instagram/Meta connectivity** — deferred; see Milestone 11B's
  completion report and `docs/INSTAGRAM_LIVE_VALIDATION.md`.

## Health/readiness — an open question for the operator

This service has **no HTTP server** — it is a single long-polling worker
process. The only signal currently available to a container orchestrator
is process liveness (does the process exit, and with what code).

**This repository cannot determine on its own whether JustRunMy.App
requires an HTTP health/readiness endpoint for a background-worker-type
deployment.** If it does, that is a small, well-scoped addition (a
minimal HTTP server exposing a static `200 OK`) — but it should be built
only once confirmed necessary. Adding it speculatively would mean
introducing a new port, and either a new dependency or a stdlib
`http.server` thread, to a process that has never needed one, for a
requirement that may not exist. **Recommendation: confirm JustRunMy.App's
actual worker-process requirements (does it require an HTTP health
check, and if so what path/port) before the next deployment attempt,**
and treat adding one as a small, separate, explicitly-scoped follow-up
if confirmed necessary.

## GitHub Actions deployment pipeline (Milestones 11D, 11D.1)

`.github/workflows/deploy-social-agent.yml` (at the **monorepo root**,
alongside the NRC Website's own `deploy-production.yml` — GitHub only
discovers workflows in that one location) automates deploying this
application to JustRunMy.App. **No real deployment has been run through
it yet.** Follow this section in order — the dry run must succeed before
you ever push to `main` or run a real deployment.

### Only `nrc-social-agent/` is ever deployed (Milestone 11D.1)

This repository is a monorepo with three independent projects on one
`main` branch — "NRC Website/", "nrc-ai-agents/", and this one. The
workflow uses Git's native `git subtree split --prefix=nrc-social-agent`
to produce a synthetic commit containing **only** the tree currently at
`nrc-social-agent/`, with that content at the **root** — not nested
inside an `nrc-social-agent/` directory, and with no trace of the sibling
projects. That commit, not `HEAD`, is what gets pushed to JustRunMy.App.
This is not a second, separately-maintained deployment repository and
never permanently rewrites this repository's own history — the split
commit exists only for the duration of the workflow run and is only ever
pushed to JustRunMy.App's remote, never back to `origin`.

Both a dry run and a real run generate and validate this subtree commit
(confirming `Dockerfile`/`requirements.txt`/`src` are present at its root
and that `NRC Website`/`nrc-ai-agents`/`nrc-social-agent` are absent) —
only the actual `git push` to JustRunMy.App is skipped on a dry run.

### 1. Create JustRunMy.App's Git-based deploy target

Set this up directly in JustRunMy.App (not something this repository can
do for you): create the application, and obtain its complete Git
deployment remote URL (credentials embedded) — JustRunMy.App's dashboard
typically presents this as a single, ready-to-use `git remote add`-style
URL. Confirm the exact branch name it expects (`deploy`, per the
workflow's own push target) before your first real run; if it differs,
the workflow's one `git push` line is the only place that needs to
change.

### 2. Add the one GitHub secret

Repo → **Settings → Secrets and variables → Actions → New repository
secret**:

| Secret name | Value |
|---|---|
| `JUSTRUNMYAPP_GIT_URL` | The complete Git deploy remote URL from Step 1, credentials included |

A single, already-complete URL secret is used deliberately instead of
separate username/password secrets concatenated into a URL by the
workflow itself: constructing a URL from separate parts would require
this workflow to URL-encode either value to stay safe against a password
containing a character like `@` or `:` (both special inside a URL's
userinfo section) — a single pre-built secret has no such risk, since
nothing here parses or reconstructs it.

GitHub automatically masks this value if it ever appears in a workflow
log — but the workflow also never echoes it directly, never enables
shell tracing (`set -x`) around the step that uses it, and removes the
temporary Git remote it creates via a shell `trap` that fires whether the
push succeeds or fails, never leaving it configured either way.

**If `JUSTRUNMYAPP_GIT_USERNAME`/`JUSTRUNMYAPP_GIT_PASSWORD`/
`JUSTRUNMYAPP_GIT_REPO` were ever actually configured as secrets under
the previous (Milestone 11D) design, rotate/revoke those credentials at
JustRunMy.App before relying on this workflow** — even though no real
deployment was ever run with them, any credential that existed as a
GitHub secret should be treated as potentially exposed and rotated
before first real use, not assumed safe because this codebase never used
it.

Runtime configuration (`TELEGRAM_BOT_TOKEN`, AWS credentials,
`META_ACCESS_TOKEN`, etc. — see `.env.example`) is a **separate** set of
values, configured directly in JustRunMy.App's own environment-variable
settings for the running application, never as a GitHub secret and never
passed through this workflow — this workflow only gets the code there; it
never supplies runtime configuration, and no runtime application secret
is ever passed to GitHub Actions.

### 3. A prerequisite this workflow cannot satisfy on its own

`git subtree split` operates on this repository's actual Git commit
history. As of this document, `nrc-social-agent/` has never been
committed to this monorepo at all — a real run's "Generate nrc-social-agent
subtree commit" step will fail with an explicit error until at least one
commit under `nrc-social-agent/` exists on the branch being deployed.
This is expected and by design — this milestone does not commit anything
on your behalf (see the standing "do not commit" constraint this project
operates under). Once `nrc-social-agent/` is committed normally (as any
other change to this repository would be), the subtree split works
exactly as validated below.

### 4. Run the first dry run (do this before touching `main`)

1. Push the workflow file (and, per Step 3, at least one commit under
   `nrc-social-agent/`) to a **branch other than `main`** first (or open
   a PR) so it doesn't trigger a real deployment via the `push` trigger.
2. Go to **Actions → Deploy Social Agent to Production (JustRunMy.App) →
   Run workflow**.
3. Leave **dry_run** checked (`true` — this is the default).
4. Run it.

A successful dry run shows: dependency install succeeds, `pytest` passes
(all 1200+ tests), the Docker build succeeds, "Generate nrc-social-agent
subtree commit" prints a commit SHA, "Validate subtree contents" prints
the root-level file list and confirms the layout, "Determine dry-run
mode" prints `==> DRY RUN: ...`, the "Deploy to JustRunMy.App (git push)"
and "Validate deployment secret is configured" steps are both skipped
entirely (not failed — check for the gray "skipped" marker, not a green
check), and the job summary shows `Deployment: skipped (dry run)`.

### 5. First real deployment (manual, still not via `main`)

1. **Actions → Deploy Social Agent to Production (JustRunMy.App) → Run
   workflow.**
2. This time, uncheck **dry_run**.
3. Run it, and watch the "Deploy to JustRunMy.App (git push)" step —
   this time it actually pushes the subtree commit.
4. In JustRunMy.App's own dashboard, confirm it received the push, that
   the repository root it sees matches `nrc-social-agent/`'s own
   contents (not a nested subdirectory, not the wider monorepo), and
   that its own build succeeded.
5. Configure the application's runtime environment variables directly in
   JustRunMy.App (Step 2's note above), then start/restart it.
6. Do **not** proceed to any Instagram operational validation yet — that
   is `docs/INSTAGRAM_LIVE_VALIDATION.md`'s own separate, explicitly-
   authorized process.

Only after this manual real run succeeds should you merge/push this
workflow file to `main` — from that point on, any push to `main` that
touches `nrc-social-agent/` (or the workflow file itself) deploys
automatically.

### 6. How automatic deployment works

A push to `main` that changes anything under `nrc-social-agent/` (or the
workflow file itself — the workflow is `paths`-filtered so unrelated
changes to "NRC Website/" or "nrc-ai-agents/" never trigger a
social-agent deployment) triggers a **real deployment**, never a dry run
— only manual `workflow_dispatch` runs can be dry runs. The `concurrency`
block (`group: nrc-social-agent-production`, `cancel-in-progress: true`)
mirrors the website workflow's own philosophy exactly: a second push
lands while a deployment is still running, the in-flight one is
cancelled and the newer commit's deployment takes over.

### 7. Fast-forward behavior and force-push (verified, not assumed)

The workflow pushes the subtree commit with a plain, non-forced push.
This was tested directly, not assumed: `git subtree split` is
deterministic — given the same source history and the same `--prefix`,
it produces the **exact same commit SHA** every time, even from a
completely fresh, independent clone with no local subtree-split cache
carried over (exactly how every GitHub Actions run starts). Two
consecutive splits, each from a separate fresh clone simulating two
separate CI runs, correctly fast-forwarded against each other with no
force needed. Redeploying an *older* commit (a manual rollback) was also
tested, and — correctly — was rejected as non-fast-forward, since that's
inherently a "go backward" operation.

**Practical implication:** ordinary forward deployments never need
`--force`, and this workflow does not use it. If a real push to
JustRunMy.App is ever rejected as non-fast-forward on what should be a
normal forward deployment (not a rollback), that means JustRunMy.App's
`deploy` branch was changed by something other than this workflow — treat
that as a signal to investigate, not a reason to reflexively add
`--force`. Confirm with JustRunMy.App's own documentation whether a force
push is ever the expected, safe pattern for their Git-deploy model before
changing the one `git push` line in the workflow — this repository has no
way to confirm that on its own, and it remains an open operational
question specific to their platform (as distinct from the fast-forward
behavior above, which is now a verified fact about `git subtree split`
itself, not a platform-specific unknown).

### 8. Roll back to a previous commit

There is no built-in "undo" for a Git-push deployment — rolling back
means redeploying the code as it existed at an earlier commit. **Prefer
revert-and-redeploy over a forced rollback push** — it produces a *new*,
forward commit (so it fast-forwards normally, per Step 7, no force ever
needed) and leaves a clear, reviewable Git history of what happened. Same
philosophy the website's own `DEPLOYMENT.md` documents:

**Option A — revert and redeploy (preferred):**
```bash
git revert <bad-commit-sha>
git push origin main
```
This triggers a fresh automatic deployment (Step 6) of the reverted
`nrc-social-agent/` content — a normal forward subtree split and push,
not a special case.

**Option B — manually redeploy an old commit (emergency use only):**
1. **Actions → Deploy Social Agent to Production (JustRunMy.App) → Run
   workflow**, switching the branch selector to the specific commit/tag
   you want.
2. Uncheck `dry_run` and run.

This may fail as non-fast-forward if JustRunMy.App's `deploy` branch has
already moved past that point (Step 7) — that failure is correct
behavior, not a bug; Option A avoids it entirely.

### 9. Local equivalents (reference)

```bash
cd nrc-social-agent
pip install -r requirements-dev.txt
pytest
docker build -t nrc-social-agent:local .
```

To inspect what the subtree split would produce, from the monorepo root:

```bash
git subtree split --prefix=nrc-social-agent
git ls-tree --name-only <the-resulting-commit-sha>
```

No linter or formatter runs in the workflow because none is configured in
this repository yet — inventing one in CI that the codebase was never
written against would just produce noise, not signal.

## Preparing for the next live validation

No code changes were made or needed to `src/execution/live_validation.py`,
`src/publisher/instagram/`, or any Milestone 10/11A/11B file — this
milestone touched only deployment-adjacent documentation
(`README.md`'s S3 setup section) and added this document. The repository
remains exactly as ready for a real supervised Instagram live validation
as Milestone 11B left it (see that milestone's completion report); this
milestone only made the *path to a running deployment* more reliable.
