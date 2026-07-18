# NRC Website — Production Deployment

Automated deployment of `NRC Website/dist/` to GoDaddy cPanel shared hosting via FTPS, triggered by `.github/workflows/deploy-production.yml`.

**No real deployment has been run yet.** Follow this document in order — the dry run (Section 4) must succeed before you ever push to `main` or run a real deployment.

---

## 1. Create the restricted GoDaddy FTP account

Do this in cPanel (GoDaddy → your hosting account → cPanel admin → **FTP Accounts**):

1. Under **FTP Accounts → Add FTP Account**, choose a username (e.g. `nrc-deploy`).
2. Set **Directory** to the production deploy path:
   ```
   public_html/nrculture.com/deploy
   ```
   cPanel will scope this new account's FTP root to exactly that directory — it will not be able to see or reach `public_html/` itself, other domains, mail folders, or logs.
3. Set a strong, unique password (a password manager-generated one — this becomes `GODADDY_FTP_PASSWORD`, never reused elsewhere).
4. Set quota to "Unlimited" (or a sensible cap) — a quota that's too small will cause silent upload failures.
5. Save.

**Why a dedicated account:** if these GitHub Actions credentials were ever leaked, they'd only expose this one directory — not the rest of the hosting account, other domains on the same cPanel account, email, or logs.

This workflow is built assuming this dedicated account exists and is already rooted at the deploy directory. If you're ever unable to create one and must fall back to the primary cPanel FTP account, tell me before running anything — the workflow's `server-dir: ./` would need to change to `public_html/nrculture.com/deploy/` in that case, since the primary account's root is the whole hosting account, not this one directory.

## 2. Identify the FTP hostname

In cPanel, under **FTP Accounts**, each account's configuration page shows an explicit **FTP Server** address — typically either your domain itself (`nrculture.com`) or a GoDaddy-assigned hostname like `ftp.nrculture.com` or a server-specific name (e.g. `ipXXX.ipXXXXXX.hostname.com`). Use exactly what cPanel shows for this account, not the domain name by default — GoDaddy's FTP hostname does not always match the public site domain.

## 3. Add the four GitHub secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**. Add all four:

| Secret name | Value |
|---|---|
| `VITE_DISCOVERY_FORM_ENDPOINT` | The deployed Apps Script `/exec` URL (same value as your local `.env.local`) |
| `GODADDY_FTP_SERVER` | The FTP hostname from Section 2 |
| `GODADDY_FTP_USERNAME` | The dedicated FTP account's full username (cPanel often shows this as `username@nrculture.com` or `cpanelacct_username` — use exactly what cPanel displays) |
| `GODADDY_FTP_PASSWORD` | The password you set in Section 1 |

GitHub automatically masks any of these values if they ever appear in a workflow log — but the workflow also never echoes them directly.

## 4. Run the first dry run (do this before touching `main`)

Once the workflow file and secrets both exist:

1. Push the `.github/workflows/deploy-production.yml` file to a **branch other than `main`** first (or open a PR) so it doesn't trigger a real deployment via the `push` trigger.
2. Go to **Actions → Deploy to Production (GoDaddy) → Run workflow**.
3. Leave **dry_run** checked (`true` — this is the default).
4. Run it.

### What successful dry-run logs should show

- **"Set up Node.js 20"** and **"Install dependencies (npm ci)"** both succeed with no red text.
- **"Build (Vite)"** completes and the **"Verify build output"** step prints a `find dist -type f` listing including `dist/index.html`, `dist/privacy.html`, and `dist/assets/...` — confirms the build produced the expected files before anything is sent anywhere.
- **"Determine dry-run mode"** prints exactly: `==> DRY RUN: no files will be uploaded. This only lists what would change.`
- **"Deploy to GoDaddy (FTPS)"** step:
  - Connects successfully (no `ECONNREFUSED`, no auth error) — confirms **authentication succeeds**.
  - Log shows the connection negotiated TLS (confirms **FTPS succeeds**, not a silent plain-FTP fallback).
  - Log lists the remote directory it's comparing against — with `server-dir: ./`, this should show file paths *without* any `public_html` or `nrculture.com` prefix (e.g. `./index.html`, `./assets/...`), confirming **the FTP root already resolves directly to the deploy folder**, matching that `server-dir: ./` targets only the NRC deploy folder, not a parent.
  - Because `local-dir` is scoped to `NRC Website/dist/`, the change-list only ever references files from that build output — confirming **only dist contents are considered**.
  - No output ever references `public_html`, other domains, or anything above the deploy directory — confirming **no parent directories are touched**.
  - It reports what it *would* upload/update/delete, but the job succeeds having made zero real changes.

If FTPS fails specifically (and only after confirming the credentials themselves are correct — a bad password will also "fail," but that's not an FTPS problem), see the one-line fallback in Section 11.

Only proceed to Section 5 once a dry run has produced clean output like this.

## 5. First real deployment (manual, still not via `main`)

1. **Actions → Deploy to Production (GoDaddy) → Run workflow.**
2. This time, uncheck **dry_run**.
3. Run it, and watch the same log sections — this time the FTP step will actually upload.
4. Immediately do the verification in Section 8.

Only after this manual real run succeeds should you merge/push this workflow file to `main` — from that point on, every push to `main` deploys automatically (Section 7).

## 6. (Reference) Local build + env setup

```bash
cd "NRC Website"
npm ci
npm run build
```

`VITE_DISCOVERY_FORM_ENDPOINT` must be present at build time (from `.env.local` for local builds, from the GitHub secret in CI) — Vite bakes it into the static output at build time, not at runtime. The workflow never creates or uploads a `.env.local` file; the variable only ever exists as an in-memory environment variable for the single `npm run build` step.

## 7. How automatic deployment works

Any push (including a merged PR) to `main` triggers the workflow with `dry_run` not applicable — pushes always run as a **real deployment**, never a dry run (only manual `workflow_dispatch` runs can be dry runs). The `concurrency` block (`group: nrc-production`, `cancel-in-progress: true`) means if a second push lands while a deployment is still running, the in-flight one is cancelled and the newer commit's deployment takes over — you'll never have two deployments racing each other or deploying out of order.

## 8. Verify `nrculture.com` after deployment

- Open `https://nrculture.com/` in a private/incognito window (avoids stale cache) — confirm the homepage loads and reflects your latest changes.
- Open `https://nrculture.com/privacy.html` — confirm it loads.
- Open browser dev tools → Network tab, reload, confirm no 404s for any `/assets/...` or `/media/...` file.
- Confirm the discovery-call form still submits successfully (this exercises the real `VITE_DISCOVERY_FORM_ENDPOINT` baked into this specific build).

## 9. Roll back to a previous commit

There is no built-in "undo" button for FTP deployment — rolling back means redeploying the *old* build output. Two options:

**Option A — revert and redeploy (preferred, keeps history honest):**
```bash
git revert <bad-commit-sha>
git push origin main
```
This creates a new commit undoing the change and triggers a fresh automatic deployment of the reverted code.

**Option B — manually redeploy an old commit (emergency use):**
1. **Actions → Deploy to Production (GoDaddy) → Run workflow**, but first switch the branch selector at the top of the "Run workflow" dialog to the specific commit/tag you want (or temporarily check out that commit on a throwaway branch and point the dispatch at it).
2. Uncheck `dry_run` and run.

Prefer Option A — it leaves a clear, reviewable Git history of what happened, rather than a deployment whose corresponding commit is hard to identify later.

## 10. What the FTP synchronization state file does

`SamKirkland/FTP-Deploy-Action` is **state-based**: after each deployment it writes a small file — configured here as `.ftp-deploy-sync-state.json` — recording exactly which files it deployed. On the *next* run, it downloads that same state file from the server first, compares it against the new `dist/` build, and uploads only what changed, while removing files that were part of a previous deployment but no longer exist in the new build (e.g., an old hashed `main-<oldhash>.js` after a rebuid produces `main-<newhash>.js`).

Because `local-dir`/`server-dir` are scoped to the dedicated FTP account's root (Section 1), this state file is written to `./.ftp-deploy-sync-state.json` **relative to that same restricted root** — i.e., inside `public_html/nrculture.com/deploy/` itself, never anywhere else on the hosting account. It only ever tracks files *this workflow* has deployed.

## 11. Why `dangerous-clean-slate` must stay disabled

`dangerous-clean-slate: true` would delete **everything** in `server-dir` first — including files the state file has never heard of. Since the state file only knows about files this workflow deployed, anything already sitting in the deploy directory that predates this workflow (an `.htaccess` file, a domain-ownership verification file GoDaddy or a search-console placed there, or anything manually uploaded later) would be silently destroyed on the very first clean-slate run, with no way to tell what was lost. This workflow sets `dangerous-clean-slate: false` explicitly and must never be changed without a full manual audit of the live directory's contents first.

**FTPS→plain-FTP fallback (only if the dry run's FTPS connection genuinely fails, not for any other error):** change the single line `protocol: ftps` to `protocol: ftp` in `.github/workflows/deploy-production.yml`. Do not make this change automatically or "just in case" — only after confirming via the dry-run logs that the failure is specifically a TLS/FTPS negotiation error, not a wrong hostname/username/password (which would fail identically either way).

## 12. How `.htaccess` and other unrelated files are preserved

Because the state-based sync only ever acts on files it previously deployed (tracked in the state file from Section 10), and `dangerous-clean-slate` is off, any file already present in the deploy directory that this workflow didn't put there — `.htaccess`, domain-verification files, anything hosting-generated, anything you upload manually later — is left completely untouched by every run, both dry and real. The very first real deployment (Section 5) only adds/updates the files from this build; it does not delete anything pre-existing, since the initial state file is empty (nothing has been "previously deployed" yet from this workflow's point of view).

## Stale hashed assets — safe manual cleanup later

Vite fingerprints filenames (`main-<hash>.js`, `favicon-32-<hash>.png`, etc.), so every rebuild that changes a file's contents produces a *new* filename. The state-based sync (Section 10) already removes old ones automatically on each deploy going forward. If you ever want to manually verify there's no drift, connect with an FTP client using the dedicated account and compare the remote `assets/` folder against the current `dist/assets/` — but do not run any bulk-delete tool against the directory outside of this workflow, since that bypasses the state file's own tracking.
