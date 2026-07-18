# Discovery Call form — setup & deployment

## 1. Deploy the Apps Script

1. Sign in to Google as **nrbrandculture@gmail.com** (this must be the account that authorizes the deployment — it's the account that will send the confirmation/notification emails and write to the sheet).
2. Open the target spreadsheet: `https://docs.google.com/spreadsheets/d/18wgXW4Lt0W50L-SiRREFvTIxHJQaNjPWPqhL8evdZBQ/edit`
3. **Extensions → Apps Script**.
4. Delete the default `Code.gs` boilerplate and paste in the contents of `google-apps-script/Code.gs` from this repo.
5. **Deploy → New deployment**.
   - Select type: **Web app**.
   - Execute as: **Me (nrbrandculture@gmail.com)**.
   - Who has access: **Anyone**.
6. Click **Deploy**. The first deployment will prompt an OAuth consent screen — approve it. It requests:
   - **Send email as you** (used by `GmailApp.sendEmail`)
   - **See, edit, create, and delete your Google Sheets spreadsheets** (used to append rows)
   No other scopes are required — there's no external fetch and no Drive access (the email header is text-based, not an image).
7. Copy the deployment's **Web app URL** (ends in `/exec`).

## 2. Point the website at it

In the `NRC Website` project root, create `.env.local` (already gitignored):

```
VITE_DISCOVERY_FORM_ENDPOINT=https://script.google.com/macros/s/XXXXXXXX/exec
```

Rebuild (`npm run build`) — Vite bakes this value into the static output at build time, not at runtime. For an actual production deploy, whichever host/CI builds the site needs this same variable set in its build environment (or a `.env.production` file present at build time).

## 3. Re-deploying after editing Code.gs

Every time you change `Code.gs`, you must **Deploy → Manage deployments → edit (pencil) → New version** and deploy again — saving the script alone does not update the live `/exec` URL's behavior.

## Notes on email sending

- Emails are sent via **GmailApp**, from `nrbrandculture@gmail.com`, displaying as **"Team NRC"** in the recipient's inbox (set via the `name` option — the underlying address is unchanged).
- Consumer Gmail accounts have a **~100 recipients/day** Apps Script sending quota (each submission uses 2 sends — customer + internal — so effectively ~50 submissions/day). If `nrbrandculture@gmail.com` is ever moved to Google Workspace, this quota rises substantially (~1,500/day).
- The confirmation email uses a **text-based NRC header** (not an image). `nrculture.com` was checked directly during planning and found to be serving a stale build with no working asset path at the time, and Vite content-hashes this filename on every rebuild anyway — so a hosted image URL isn't reliable right now. Once the site has a stable, unhashed public asset path, swapping in a real logo image is a small, isolated change (add an `<img>` inside `buildCustomerHtml()` in `Code.gs`).

## Sheet header

`Code.gs` verifies (and repairs, if missing/mismatched) the header row of the **`NRC Discovery Call Leads`** tab on every run:

```
Submitted At | First Name | Last Name | Email Address | Brand / Company | Website or Instagram | Service Interested In | Tell Us About Your Brand | Source | Status
```

If the tab doesn't exist yet, it will be created automatically on first submission.

---

# Test checklist

Run these against a real deployment (a mock server was used for local frontend testing during implementation, but the real Apps Script's CORS/response behavior should be spot-checked once deployed — see below).

## Post-deployment sanity check (do this first)
- [ ] Open the deployed `/exec` URL directly in a browser tab — should return `{"ok":true,"message":"NRC discovery form endpoint is running."}` (confirms the script deployed correctly; this GET path isn't used by the website itself).
- [ ] From the browser console on any page, run a real cross-origin POST test against the deployed URL (same shape as the site's own fetch) and confirm the response is readable (not opaque) — the CORS/redirect mechanics were verified against the *previous* deployment during planning, but the *new* JSON-returning code should be spot-checked once live.

## Form behavior
- [ ] Submit with all fields empty — First Name gets focus, all required fields highlighted (First, Last, Email, Brand, Service, Brief — Website stays optional).
- [ ] Submit with an invalid email format — email field flagged.
- [ ] Submit a valid, complete form — button shows "Submitting…" and is disabled during the request.
- [ ] On success: success panel shows exactly "Thank you for reaching out to NRC. We've received your discovery call request and will get back to you within 48 hours. A confirmation email has been sent to your inbox."; form is cleared/hidden.
- [ ] Check the sheet: a new row appears in **NRC Discovery Call Leads** with the correct values, Source = "NRC Website", Status = "New", a correctly IST-formatted timestamp.
- [ ] Check `nrbrandculture@gmail.com` inbox: confirmation-style email arrived in the **customer's** inbox (use a real test address you control) from "Team NRC", and a separate internal notification arrived at `nrbrandculture@gmail.com` with subject `New NRC Discovery Call Request — {Brand}`.
- [ ] Resubmit the **exact same** form values within ~10 minutes — no second row is added, no duplicate emails sent, success message still shows.
- [ ] Wait past 10 minutes (or change one field) and resubmit — a new row **is** created.
- [ ] Same email address, a **different** brief/brand — new row created immediately (not treated as duplicate).
- [ ] Rapid double-click the submit button — only one request fires (client-side `submitting` guard).
- [ ] Fill the honeypot field via devtools (`document.getElementById('audit-hp').value = 'x'`) and submit — Apps Script should reject it (check the Apps Script execution log; no row should appear).
- [ ] Submit a value longer than the maxlength in devtools (bypassing the HTML attribute) — Apps Script truncates it server-side rather than rejecting or crashing.
- [ ] Turn off wifi/simulate a network failure and submit — after ~15s, failure message shows verbatim: "We couldn't submit your request right now. Please try again in a moment or contact us directly at nrbrandculture@gmail.com."; all entered values remain in the fields; button re-enabled.
- [ ] After any failure, submit again successfully without needing to refresh the page.

## Cross-browser / responsive
- [ ] Chromium, WebKit, Firefox — full success path.
- [ ] 1440 / 1024 / 768 / 430 / 390 / 375 / 320px — no layout shift, no overflow from the added honeypot/field changes.
- [ ] Keyboard-only: tab through the form — honeypot is skipped, focus indicators visible on Last Name and Service (newly required).
