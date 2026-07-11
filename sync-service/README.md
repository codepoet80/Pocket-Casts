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

## Deploy (nginx, beside the PHP catalog)

Server is nginx; the PHP catalog lives at `/var/www/podcasts/`. Clone this repo alongside
it and run the service from inside the repo (so it can import the `pocketcasts` package).

```sh
# On the server, as a user that can write to /var/www/podcasts
cd /var/www/podcasts
git clone https://github.com/codepoet80/Pocket-Casts.git
cd Pocket-Casts/sync-service
bash deploy/install.sh          # creates venv, installs deps, health-checks the app
```

`install.sh` sets up the app and prints the remaining sudo steps:

1. Install + start the service (unit paths already point at
   `/var/www/podcasts/Pocket-Casts/sync-service`):
   ```sh
   sudo cp deploy/pocketcasts-sync.service /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now pocketcasts-sync
   sudo systemctl status pocketcasts-sync      # confirm it's running
   ```
   If `/var/www/podcasts/Pocket-Casts` isn't owned by `www-data`, either `chown -R www-data`
   it or change `User=`/`Group=` in the unit to the owning user.
2. Proxy `/sync/` to gunicorn — add `deploy/nginx.conf.snippet` inside the
   `server {}` block for `podcasts.webosarchive.org`, then:
   ```sh
   sudo nginx -t && sudo systemctl reload nginx
   ```
3. Verify: `curl https://podcasts.webosarchive.org/sync/health` → `{"status":"ok"}`
   (also try plain `http://` — that's what the retro device uses).

(An Apache snippet is in `deploy/apache.conf.snippet` if you ever need it.)

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
