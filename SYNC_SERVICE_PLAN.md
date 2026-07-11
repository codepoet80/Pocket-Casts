# Pocket Casts Sync Service — Plan

A design document for adding **optional** Pocket Casts playback sync to
[drPodder](https://github.com/codepoet80/webos-drpodder), without changing how the
existing catalog works.

> Status: plan only. Feasibility verified against the live Pocket Casts API
> (July 2026) using the modernized `pocketcasts` library in this repo.

## 0. Workspace — all three projects are local

All three repos now live side-by-side under `~/Projects/pocketcasts-api/`, so this can be
worked across all three at once:

| Folder | Repo | Role |
|---|---|---|
| `Pocket-Casts/` | `codepoet80/Pocket-Casts` (fork) | The Python `pocketcasts` library (modernized here). The sync service wraps this. |
| `webos-drpodder/` | `codepoet80/webos-drpodder` | The webOS podcast app (Mojo/JS). Gets the optional sync client. |
| `podcast-service/` | `webOSArchive/podcast-service` | The PHP catalog **backend** (PodcastIndex-backed): `search.php`, `getdetailby.php`, `mp3.php`, `image.php`, `tiny.php`. Unchanged — and the reference for the proxy + server deployment pattern the Python service should sit beside. |

---

## 1. Goal & scope

**Add, don't replace.** drPodder keeps using the existing PHP catalog
([webOSArchive/podcast-service](https://github.com/webOSArchive/podcast-service),
backed by PodcastIndex) for search, discovery, and its MP3/image proxying.
Nothing about the no-login experience changes.

**Optionally**, a user can log in to a Pocket Casts account. When they do, drPodder
can **sync playback state** with that account:

- which episodes are **played / unplayed / in progress** (`playingStatus`)
- **current playback position** for in-progress episodes (`playedUpTo`, seconds)
- *(stretch)* **starred** episodes and the **subscription list**

The payoff: someone listening on Pocket Casts on their phone can pick up where they
left off in drPodder on a Pre/TouchPad, and vice-versa.

**Explicitly out of scope:** replacing catalog/search/discovery, changing the proxy,
or requiring an account for any existing feature.

---

## 2. Is it possible? Yes — and here's the proof

The hard part of any sync is **identity matching**: drPodder identifies things by RSS
feed URL and episode enclosure URL; Pocket Casts identifies them by its own opaque
UUIDs. If those can't be bridged, sync is impossible. They can:

| Join level | drPodder's key | Pocket Casts' key | Result (live test) |
|---|---|---|---|
| Podcast | RSS feed URL | `podcast.url` (from `get_podcast(uuid)`) | Exact match |
| Episode | `<enclosure url>` | `episode.url` | **15 / 15 exact matches** on This American Life |

Both sync directions were exercised against a real account:

- **Pull (PC → device):** `get_in_progress()` / `get_starred()` / history return each
  episode's `playedUpTo`, `playingStatus`, enclosure `url`, and `podcastUuid`. The
  device matches by enclosure URL — no Pocket Casts UUIDs ever need to touch drPodder.
- **Push (device → PC):** from a feed URL + enclosure URL, the service resolves the
  Pocket Casts podcast UUID (search by title, confirm by matching `podcast.url`) and the
  episode UUID (from the podcast's episode list), then calls `update_played_position` /
  `update_playing_status`. Verified working.

The enclosure URL is the linchpin, and it's stable because both drPodder and Pocket
Casts store the *publisher's* enclosure URL (tracking prefixes and all) verbatim.

---

## 3. Architecture

A **new, standalone sync service** sitting beside the existing catalog service. They
don't share code or state; drPodder just talks to whichever it needs.

```
                    ┌────────────────────────────────────────┐
                    │  webOS device: drPodder (Mojo/JS)        │
                    │                                          │
                    │  local DB (db.js): feeds + episodes,     │
                    │  keyed by feed URL / enclosure URL       │
                    └──────┬───────────────────────┬───────────┘
                           │ (unchanged)           │ (new, optional)
                           ▼                        ▼
         ┌──────────────────────────┐   ┌──────────────────────────────┐
         │ podcast-service (PHP)     │   │ sync-service (Python/Flask)   │
         │ search.php, getdetailby,  │   │ /login  /pull  /push          │
         │ mp3.php, image.php  …     │   │ wraps the `pocketcasts` lib   │
         │ → PodcastIndex            │   │ → api.pocketcasts.com         │
         └──────────────────────────┘   └──────────────────────────────┘
```

**Why a separate service, not bolted onto the PHP one:**
- The sync logic *is* the `pocketcasts` Python library we just modernized — reuse it
  directly instead of reimplementing Pocket Casts auth/token handling in PHP.
- Keeps the "no login" path completely untouched and un-risked.
- Can be deployed independently (even on the same host, different port/subpath).

**Suggested stack:** Flask + gunicorn behind the same Nginx/Apache. Small surface, a
handful of JSON endpoints. Could live at e.g. `http://podcasts.webosarchive.org/sync/`.

---

## 4. API surface (proposed)

All JSON. Mirrors the existing service's `{status, msg}` error convention so drPodder's
patterns carry over.

### `POST /sync/login`
Body: `{ "email": "...", "password": "..." }`
→ `{ "status": "ok", "token": "<opaque session token>" }`
The service logs in to Pocket Casts, and returns **its own** session token that the
device stores (never re-sends the Pocket Casts password). See security notes.

### `GET /sync/pull?token=…&since=<iso8601>`
Returns everything needed to update the device, keyed by URL so no PC UUIDs leak out:
```json
{
  "status": "ok",
  "episodes": [
    { "feedUrl": "https://…/rss.xml",
      "enclosureUrl": "https://…/ep123.mp3",
      "playingStatus": 2,          // 0 unplayed, 2 in-progress, 3 played
      "playedUpTo": 842,           // seconds
      "starred": false }
  ]
}
```
Source: `get_in_progress()` + `get_starred()` + history, de-duplicated.

### `POST /sync/push?token=…`
Body: playback updates from the device:
```json
{ "episodes": [
    { "feedUrl": "https://…/rss.xml",
      "enclosureUrl": "https://…/ep123.mp3",
      "playingStatus": 3,
      "playedUpTo": 1731 } ] }
```
Service resolves URLs → PC UUIDs (§2) and calls `update_playing_status` /
`update_played_position`. Returns per-item ok/failed so the device can retry.

### `GET /sync/subscriptions?token=…` *(stretch)*
→ `{ "feeds": [ { "feedUrl": "…", "title": "…" } ] }` from `get_subscribed_podcasts()`,
so a first-time login can offer to import the PC subscription list into drPodder.

---

## 5. Sync model & conflict handling

- **Position/status is last-writer-wins per episode.** Podcast playback sync is not a
  hard consistency problem; a `playedUpTo` a few seconds off doesn't matter. Use the
  larger `playedUpTo` (or the newer timestamp) when both sides changed.
- **Start read-only (pull), then add push.** Pull alone already delivers most of the
  value and can't corrupt the PC account. Add push once it's proven.
- **Batch pushes.** Retro devices are slow and may be offline often; drPodder should
  queue local playback changes and flush them in one `/sync/push` when connectivity and
  the user allow.
- **Resolution cache.** The push-side feed-URL→UUID and enclosure-URL→UUID lookups are
  the expensive part (a search + an episode-list fetch). Cache them server-side per
  feed so repeated syncs are cheap. This mirrors how `podcast-service` already caches.

---

## 6. Security & privacy

**Transport is a solved problem in this ecosystem.** Getting modern HTTPS to/from retro
webOS devices has already been handled in the related projects (the proxy approach behind
`podcast-service` / `webos-podcastdirectory`, and other webOSArchive services). So the
device↔server hop is **not** a blocker for sending credentials — reuse whatever TLS/proxy
pattern those projects already use. Earlier drafts of this plan overstated this as the top
risk; it isn't.

Remaining hygiene (still worth doing, but routine):

- **Never store the password.** Exchange it for a Pocket Casts token immediately at
  `/sync/login` and persist only the token, mapped to the device's opaque session token.
- **Dedicated account is still a nice default.** Recommending a drPodder-specific Pocket
  Casts account (as the test account `curator@webosarchive.org` demonstrates) limits blast
  radius, but it's a convenience, not a requirement.
- **Token lifecycle.** Pocket Casts issues a JWT that expires. Handle re-login/refresh
  transparently and, on `401`, tell the device to re-auth. (The current library does a
  fresh login per `Pocketcasts()` construction — fine for a request-scoped service; add
  refresh if you keep long-lived sessions.)
- **Don't log credentials or tokens.** Easy to leak into request logs.

---

## 7. drPodder client changes

Minimal and additive (JS/Mojo):

- **Settings:** an optional "Pocket Casts account" section — email/password → calls
  `/sync/login`, stores the returned token in prefs. A "Sync now" action + optional
  sync-on-launch.
- **New model** (e.g. `app/models/syncservice-model.js`, parallel to
  `directoryservice-model.js`): wraps `/sync/pull` and `/sync/push`.
- **Apply pull results:** look up local episodes by enclosure URL (confirm drPodder's
  `episode.js` / `db.js` store the enclosure URL as an addressable key — it should, since
  that's the download URL) and update played state + position.
- **Collect push updates:** when the user finishes/scrubs an episode, queue a
  `{feedUrl, enclosureUrl, status, position}` record for the next flush.
- No change to any existing catalog, subscription, or playback code paths when the user
  hasn't logged in.

---

## 7a. Deployment — to work out next time

**Open task for next session:** run the Python sync service on the server, alongside the
existing PHP catalog behind nginx (or Apache).

What the local `podcast-service/` tells us about the existing deployment:
- **Web root is `/var/www/podcasts/`** (from `podcast-cleanup.sh`). The PHP service is
  served from there; the sync service naturally lives at `/var/www/podcasts/sync/` (proxied)
  or a sibling path.
- **Secrets convention:** copy `secrets-example.php` → `secrets.php` (gitignored). The
  Python service should mirror this — a non-committed config/env file for any Pocket Casts
  app-level config + token-signing key. `.gitignore` already excludes `secrets.php` and
  `/cache`.
- **Server could be either Apache or nginx** — `secrets-example.php`'s `hideFilePath`
  supports both `X-Sendfile` (Apache) and `X-Accel-Redirect` (nginx). **First action next
  time: confirm which one the live box runs**, since it dictates the proxy config.
- **Cron cleanup exists** (`podcast-cleanup.sh` deletes cache files older than 90 min) —
  the sync service's resolution cache (§5) can follow the same pattern.

To settle:
- **Process model.** Flask isn't served directly — front it with `gunicorn` (or `uwsgi`)
  as a WSGI app under `systemd` so it restarts on boot/crash.
- **Web-server wiring.** Reverse-proxy a subpath to the gunicorn socket:
  - nginx: `location /sync/ { proxy_pass http://127.0.0.1:8001/; }`
  - Apache: `ProxyPass /sync/ http://127.0.0.1:8001/` (needs `mod_proxy` + `mod_proxy_http`)
  so the sync service shares the same host/TLS as `podcasts.webosarchive.org` and the PHP app.
- **Coexistence.** The Python service doesn't touch the PHP app; independent processes
  behind the same web server.
- **Python availability.** Confirm the box has Python 3 + `pip` (to install `requests`,
  Flask, gunicorn), or containerize.

> I (Claude) can stand this up next time — fastest path: read the live server's actual
> web-server config (nginx site or Apache vhost for `/var/www/podcasts/`), then mirror it
> for the Python app. Having the PHP repo local now means I can match its conventions
> exactly.

## 8. Roadmap

1. **Spike (½ day):** stand up Flask with `/sync/login` + `/sync/pull`; curl it with the
   test account; confirm the URL-keyed payload. *(All underlying calls already work.)*
2. **Pull MVP:** `syncservice-model.js` in drPodder consumes `/sync/pull` and marks
   played/in-progress episodes. Read-only, safe. Ship behind the optional login.
3. **Push:** add `/sync/push` + the resolution cache; queue+flush on the device.
4. **Subscription import (stretch):** offer to pull the PC subscription list on first
   login.
5. **Hardening:** token refresh, caching, rate limiting, request logging hygiene.

Rough effort: service side is small (the library does the heavy lifting) — a few days
for pull+push. The drPodder-side integration and testing on real hardware is the larger
share.

---

## 9. Open questions / to verify

- **drPodder's local episode key.** Confirm `episode.js`/`db.js` can look up an episode
  by enclosure URL (or GUID). If it only keys by GUID, note the RSS `<guid>` is *not*
  what Pocket Casts stores — but the enclosure URL is, so URL matching is the path.
- **History depth.** `get_in_progress`/`get_starred` are bounded; a full "everything
  I've ever played" pull may need the history endpoint's paging. Fine for MVP (in-progress
  + recently-played is what matters).
- **Push for un-subscribed podcasts.** Resolution (§2) needs the podcast to be in Pocket
  Casts' catalog (nearly always true). Decide whether push should also *subscribe* the
  podcast in PC, or only record playback.
- **Tracking-prefix drift.** Enclosure URLs matched 15/15 here. If a publisher rotates
  tracking prefixes, exact match could miss; a filename-core fallback (also 15/15 in
  testing) is a cheap safety net worth keeping.
- **Deployment shape.** Same host as the PHP service (subpath) vs. separate — decide
  based on the existing Nginx/Apache setup.

---

## 10. Reference: relevant `pocketcasts` library calls

Everything the service needs already exists in this repo (verified live):

| Need | Library call |
|---|---|
| Login → token | `Pocketcasts(email, password)` → `._token` |
| In-progress episodes (pull) | `get_in_progress()` |
| Starred episodes (pull) | `get_starred()` |
| Subscription list (import) | `get_subscribed_podcasts()` |
| Resolve feed URL → podcast | `search_podcasts(title)` + `get_podcast(uuid).url` |
| Episode enclosure ↔ UUID | `get_podcast_episodes(pod)` → `.url` / `.uuid` |
| Write position (push) | `update_played_position(pod, ep, seconds)` |
| Write played/unplayed (push) | `update_playing_status(pod, ep, status)` |
| Write starred (push) | `update_starred(pod, ep, 1/0)` |

Episode fields available for matching/state: `.url` (enclosure), `.uuid`,
`.playing_status`, `.played_up_to`, `.starred`, `.published_at`, `.title`, `.duration`.
