# Pocket Casts Sync Service

A small Flask service that lets [drPodder](https://github.com/codepoet80/webos-drpodder)
**optionally** sync playback state (played/unplayed + position, and starred) with a
Pocket Casts account. It wraps the `pocketcasts` Python library in the parent directory.

It does **not** replace drPodder's catalog/search — that stays on the existing PHP
`podcast-service`. This is additive and only used when a user logs in to Pocket Casts.

See `../SYNC_SERVICE_PLAN.md` for the full design rationale.

## How it works

The client never sees Pocket Casts UUIDs. Everything is keyed by URL:

- **Podcast** join key = RSS feed URL (`podcast.url`)
- **Episode** join key = enclosure URL (`episode.url`) — verified matching drPodder's
  RSS enclosures 15/15 on real feeds.

The session token returned by `/sync/login` **is** the Pocket Casts bearer token, so the
service is stateless and safe across multiple workers. It's a token, not a password.

## Endpoints

| Method | Path | Params | Returns |
|---|---|---|---|
| GET | `/sync/health` | — | `{status:"ok"}` |
| POST | `/sync/login` | `email`, `password` (JSON or form) | `{status:"ok", token}` |
| GET | `/sync/pull` | `token` | `{status:"ok", episodes:[{feedUrl, enclosureUrl, playingStatus, playedUpTo, starred}]}` |
| POST | `/sync/push` | `token` + body `{episodes:[{feedUrl, enclosureUrl, playingStatus, playedUpTo, title?}]}` | `{status:"ok", results:[{enclosureUrl, ok, error?}]}` |
| GET | `/sync/subscriptions` | `token` | `{status:"ok", feeds:[{feedUrl, title, uuid}]}` |

**Token** may be passed as `?token=`, header `X-Sync-Token`, or `Authorization: Bearer`.

**playingStatus**: `0` unplayed, `2` in progress, `3` played (matches Pocket Casts).

**push note:** include `title` in each item when you can — it lets the service resolve a
feed URL to a Pocket Casts podcast even when the account isn't subscribed to it. Without
`title`, resolution only works for podcasts the account already subscribes to.

Errors use the same envelope as `podcast-service`: `{status:"error", msg:"..."}`.

## Run locally

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
PORT=8001 .venv/bin/python sync_service.py     # dev server
```

Test against the live API (uses a real Pocket Casts account):

```sh
POCKETCASTS_EMAIL=... POCKETCASTS_PASSWORD=... .venv/bin/python test_sync.py
```

## Deploy (beside the PHP catalog)

Target layout mirrors `podcast-service` at `/var/www/podcasts/`, with this service at
`/var/www/podcasts/sync/`.

1. Copy this `sync-service/` dir to `/var/www/podcasts/sync/` and create the venv there:
   ```sh
   cd /var/www/podcasts/sync
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   ```
   The library import is handled by a `sys.path` shim to the parent dir; ensure the
   `pocketcasts` package (this repo) sits one level up, or `pip install` it into the venv.
2. Install the systemd unit and start it:
   ```sh
   sudo cp deploy/pocketcasts-sync.service /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now pocketcasts-sync
   ```
3. Wire the web server to proxy `/sync/` to `127.0.0.1:8001`:
   - **nginx** — add `deploy/nginx.conf.snippet` to the site's `server {}` block.
   - **Apache** — add `deploy/apache.conf.snippet` to the vhost (`a2enmod proxy proxy_http`).
   > First confirm whether the box runs nginx or Apache, then use the matching snippet.
4. Verify: `curl https://podcasts.webosarchive.org/sync/health` → `{"status":"ok"}`.

## Config / env

- `PORT` — port gunicorn/dev server binds (default 8001).
- `SYNC_CACHE_TTL` — seconds to cache push-side URL→UUID resolution (default 3600).

No secrets file is needed: Pocket Casts credentials arrive per-request from the device
and are exchanged for a token immediately (never stored).

## Notes / limits

- The resolution cache is per-worker and in-memory. Fine for this scale; swap for Redis
  if you run many workers and want shared caching.
- `/sync/pull` combines in-progress + history + starred. History depth is whatever the
  Pocket Casts endpoint returns (recent); it is not the full lifetime history.
