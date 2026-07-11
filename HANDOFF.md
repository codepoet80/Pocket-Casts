# Pocket Casts Sync — Handoff / Verification Checklist

What I built autonomously, what's verified, and what I need **you** to
verify / deploy / test when you're back. Ordered by what unblocks what.

## TL;DR

- **Sync service (Python):** built and **fully tested against the live API**. Login →
  push → pull round-trip works (position + played/unplayed sync both directions).
- **drPodder client (webOS/JS):** implemented following existing patterns, **syntax-checked
  but NOT tested on-device** (I can't run webOS here). Pushed to a **branch**, not master.
- Catalog/search is untouched. Sync is fully optional and off until a user signs in.

Two commits went to `master` of your **Pocket-Casts** fork (library + service). The
drPodder changes are on branch **`pocketcasts-sync`** so you can review before merging.

---

## Part 1 — What's DONE and verified ✅

### Library (`Pocket-Casts/pocketcasts/`)
- `Pocketcasts.from_token(token)` — rebuild a client from a stored token (no re-login).
- `get_history()` — recently-played episodes (for pull).
- Both covered by the existing `fulltest.py` run (all methods pass).

### Sync service (`Pocket-Casts/sync-service/`)
- Flask app: `/sync/login`, `/sync/pull`, `/sync/push`, `/sync/subscriptions`, `/sync/health`.
- URL-keyed contract (no PC UUIDs leak to the client).
- `test_sync.py` passes against the live API with the test account: login, subscribe,
  push (mark in-progress @55s), pull (comes back by enclosure URL with correct
  position/status/feedUrl), cleanup. Also verified running under **gunicorn**.
- Deployment artifacts in `sync-service/deploy/`: systemd unit, nginx snippet, apache snippet.

### drPodder client (`webos-drpodder/`, branch `pocketcasts-sync`)
- `app/models/syncservice-model.js` — login/logout, pull (apply by enclosure URL),
  push queue + flush, `syncNow()`.
- `app/models/episode.js` — `syncChanged()` hooks on setListened/setUnlistened/bookmark/clearBookmark.
- `app/models/db.js` — new `Prefs.pcSync*` defaults (feature off by default).
- `app/assistants/preferences-assistant.js` + `preferences-scene.html` — a
  "Pocket Casts Sync (optional)" settings section (email/password, Sign In, Sync Now, Sign Out).
- `sources.json` — registers the new model.
- All JS syntax-checked with `node --check`; JSON validated.

---

## Part 2 — What YOU need to do 🔲

### A. Deploy the sync service  *(you asked for help with this — happy to pair next session)*
1. **Tell me / confirm: is the server nginx or Apache?** That picks the proxy snippet.
   (The PHP `secrets-example.php` supports both X-Sendfile and X-Accel-Redirect, so the
   repo alone doesn't say which is live.)
2. Copy `sync-service/` to `/var/www/podcasts/sync/`, create the venv, install requirements.
   The service imports the `pocketcasts` lib via a `sys.path` shim to its parent — so keep
   the repo layout, or `pip install` the library into the venv.
3. Install `deploy/pocketcasts-sync.service`, `systemctl enable --now`.
4. Add the matching proxy snippet (`deploy/nginx.conf.snippet` or `apache.conf.snippet`).
5. **Verify:** `curl https://podcasts.webosarchive.org/sync/health` → `{"status":"ok"}`.
6. **Verify login end-to-end:**
   `curl -X POST https://podcasts.webosarchive.org/sync/login -d email=... -d password=...`

### B. Test the drPodder client on real hardware / emulator  *(the big unknown)*
I could not run webOS, so these need your eyes:
1. Build the branch `pocketcasts-sync` into an IPK and install on a Pre/TouchPad/emulator.
2. **Preferences UI:** confirm the "Pocket Casts Sync" section renders and the
   TextField/PasswordField/Button widgets behave. (I used standard Mojo widgets but the
   exact widget attrs — e.g. `PasswordField`, activityButton `deactivate()` — are the
   most likely thing to need tweaking.)
3. **Sign in** with the test account → should banner "Signed in" and auto-sync.
4. **Pull:** with the test account subscribed to a podcast you also have in drPodder,
   confirm played/in-progress state appears on matching episodes (matched by enclosure URL).
5. **Push:** mark an episode played / scrub position in drPodder, hit **Sync Now**, then
   check play.pocketcasts.com (or the phone app) reflects it.
6. **Confirm the disabled path is inert:** with no sign-in, everything behaves exactly as
   before (no network calls, no errors).

### C. Decisions for me to implement next (your call)
1. **Auto-sync trigger.** Right now sync is manual ("Sync Now") + once right after sign-in.
   I left `Prefs.pcSyncOnUpdate` (default true) but did **not** wire it into the feed-update
   or app-launch path, to avoid touching core flows untested. Want auto-sync on launch
   and/or after feed refresh? I'll wire it once you confirm the hook point (updater-model
   vs app-assistant).
2. **Subscription import.** `/sync/subscriptions` exists and is tested, but the drPodder
   side doesn't consume it yet. Want a "Import my Pocket Casts subscriptions" button?
3. **Base URL.** Client defaults to `http://podcasts.webosarchive.org/sync/`. Change if the
   service lands elsewhere.

---

## Part 3 — Known limitations / notes

- **Resolution cache is per-worker, in-memory.** Fine for this scale; move to Redis if you
  run many workers and want shared caching.
- **Pull history depth** is whatever Pocket Casts' `/user/history` returns (recent), not a
  full lifetime history. In-progress + starred are always complete.
- **Push without `title`** only resolves podcasts the account already subscribes to. The
  drPodder client sends `feed.title`, so this is covered — just don't strip it.
- **Security:** transport is handled by your existing retro-HTTPS approach. The service
  never stores passwords (exchanges for a token immediately). Tokens are JWTs that expire;
  the client auto-logs-out on 401 and prompts re-sign-in.

## How to re-run the automated tests

```sh
# Library
cd Pocket-Casts
POCKETCASTS_EMAIL=... POCKETCASTS_PASSWORD=... python3 fulltest.py

# Sync service
cd Pocket-Casts/sync-service
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
POCKETCASTS_EMAIL=... POCKETCASTS_PASSWORD=... .venv/bin/python test_sync.py
```
