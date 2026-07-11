"""Pocket Casts sync service for drPodder.

A small Flask app that wraps the modernized `pocketcasts` library and exposes a
handful of URL-keyed JSON endpoints so drPodder (or any client) can sync playback
state with a Pocket Casts account — WITHOUT the client ever needing to know Pocket
Casts' internal UUIDs.

Design notes:
- The "session token" returned by /sync/login IS the Pocket Casts bearer token.
  This keeps the service stateless (safe across multiple gunicorn workers, survives
  restarts). It's a token, not the password. If you later want opaque server-minted
  tokens, add a shared store (Redis) and swap _client_for_token().
- Join keys are URLs, never PC UUIDs: podcasts join on RSS feed URL (podcast.url),
  episodes on enclosure URL (episode.url). Verified 15/15 against live feeds.
- Push resolution (feed URL -> PC uuid, enclosure URL -> PC episode uuid) is cached
  in-process with a TTL. Per-worker cache; fine for this scale.

See README.md for the endpoint contract and deployment.
"""
import os
import sys
import time
import threading

# Make the sibling `pocketcasts` package importable when running in-place
# (in production you'd `pip install` the library instead).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify
from pocketcasts import Pocketcasts
from pocketcasts.podcast import Podcast

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Playing-status vocabulary (matches Pocket Casts and Episode.PlayingStatus)
#   0 = unplayed, 2 = in progress, 3 = played
# ---------------------------------------------------------------------------
UNPLAYED, IN_PROGRESS, PLAYED = 0, 2, 3

# Simple in-process TTL cache for push-side URL->UUID resolution.
_CACHE_TTL = int(os.environ.get("SYNC_CACHE_TTL", "3600"))
_cache_lock = threading.Lock()
_feed_to_uuid = {}          # feedUrl -> (podcast_uuid, expires_at)
_podcast_episode_index = {}  # podcast_uuid -> ({enclosureUrl: episode_uuid}, expires_at)


def _cache_get(store, key):
    with _cache_lock:
        hit = store.get(key)
        if hit and hit[1] > time.time():
            return hit[0]
        if hit:
            del store[key]
    return None


def _cache_put(store, key, value):
    with _cache_lock:
        store[key] = (value, time.time() + _CACHE_TTL)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def _extract_token():
    """Pull the session token from query param, X-Sync-Token, or Authorization."""
    tok = request.args.get("token") or request.headers.get("X-Sync-Token")
    if not tok:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            tok = auth[7:]
    return tok or None


def _client_for_token(token):
    """Rebuild an authenticated Pocketcasts client from a stored token."""
    return Pocketcasts.from_token(token)


def _err(msg, code=200):
    """Error envelope matching the existing podcast-service convention."""
    return jsonify({"status": "error", "msg": msg}), code


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.route("/sync/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/sync/login", methods=["POST", "GET"])
def login():
    """Exchange Pocket Casts credentials for a session token.

    Accepts JSON body or form/query params: email, password.
    Returns {status:"ok", token:"<bearer>"} on success.
    """
    email = (request.json or {}).get("email") if request.is_json else None
    password = (request.json or {}).get("password") if request.is_json else None
    email = email or request.values.get("email")
    password = password or request.values.get("password")
    if not email or not password:
        return _err("email and password are required")
    try:
        pc = Pocketcasts(email, password)
    except Exception as e:
        return _err("login failed: {}".format(e))
    return jsonify({"status": "ok", "token": pc._token})


@app.route("/sync/pull")
def pull():
    """Return the account's playback state, keyed by URL.

    {status:"ok", episodes:[{feedUrl, enclosureUrl, playingStatus, playedUpTo, starred}]}
    Sources: in-progress + history + starred, de-duplicated by enclosure URL.
    """
    token = _extract_token()
    if not token:
        return _err("missing token")
    try:
        pc = _client_for_token(token)
        by_url = {}

        def absorb(episodes, starred_flag=None):
            for e in episodes:
                if not e.url or e.podcast is None:
                    continue
                rec = by_url.setdefault(e.url, {
                    "feedUrl": e.podcast.url,
                    "enclosureUrl": e.url,
                    "playingStatus": UNPLAYED,
                    "playedUpTo": 0,
                    "starred": False,
                })
                # Prefer the most-progressed status / position we see.
                status = e.playing_status if isinstance(e.playing_status, int) else UNPLAYED
                rec["playingStatus"] = max(rec["playingStatus"], status)
                try:
                    rec["playedUpTo"] = max(rec["playedUpTo"], int(e.played_up_to or 0))
                except (TypeError, ValueError):
                    pass
                rec["starred"] = rec["starred"] or bool(e.starred) or bool(starred_flag)

        absorb(pc.get_in_progress())
        absorb(pc.get_history())
        absorb(pc.get_starred(), starred_flag=True)
    except Exception as e:
        return _err("pull failed: {}".format(e))
    return jsonify({"status": "ok", "episodes": list(by_url.values())})


@app.route("/sync/subscriptions")
def subscriptions():
    """Return the account's subscribed feeds: {status:"ok", feeds:[{feedUrl, title}]}."""
    token = _extract_token()
    if not token:
        return _err("missing token")
    try:
        pc = _client_for_token(token)
        feeds = []
        for pod in pc.get_subscribed_podcasts():
            # get_subscribed_podcasts gives metadata but not the RSS url directly;
            # resolve it (cached) so drPodder gets a subscribable feed URL.
            feed_url = _resolve_feed_url(pc, pod)
            feeds.append({"feedUrl": feed_url, "title": pod.title, "uuid": pod.uuid})
    except Exception as e:
        return _err("subscriptions failed: {}".format(e))
    return jsonify({"status": "ok", "feeds": feeds})


@app.route("/sync/push", methods=["POST"])
def push():
    """Apply device-side playback updates to the Pocket Casts account.

    Body: {episodes:[{feedUrl, enclosureUrl, playingStatus, playedUpTo, title?}]}
    Returns per-item results so the device can retry failures:
    {status:"ok", results:[{enclosureUrl, ok, error?}]}
    """
    token = _extract_token()
    if not token:
        return _err("missing token")
    body = request.json or {}
    episodes = body.get("episodes", [])
    if not isinstance(episodes, list):
        return _err("episodes must be a list")

    pc = _client_for_token(token)
    results = []
    for item in episodes:
        enclosure = item.get("enclosureUrl")
        feed_url = item.get("feedUrl")
        try:
            podcast_uuid = _resolve_podcast_uuid(pc, feed_url, item.get("title"))
            if not podcast_uuid:
                raise Exception("could not resolve podcast for feed {}".format(feed_url))
            episode_uuid = _resolve_episode_uuid(pc, podcast_uuid, enclosure)
            if not episode_uuid:
                raise Exception("could not resolve episode for {}".format(enclosure))

            status = int(item.get("playingStatus", UNPLAYED))
            position = int(item.get("playedUpTo", 0) or 0)
            # One sync/update_episode call sets both status and position.
            resp = pc._make_req(
                "{}/sync/update_episode".format(pc.API),
                method="JSON",
                data={"uuid": episode_uuid, "podcast": podcast_uuid,
                      "status": status, "position": position},
            )
            if resp.status_code != 200:
                raise Exception("pocketcasts returned {}".format(resp.status_code))
            results.append({"enclosureUrl": enclosure, "ok": True})
        except Exception as e:
            results.append({"enclosureUrl": enclosure, "ok": False, "error": str(e)})
    return jsonify({"status": "ok", "results": results})


# ---------------------------------------------------------------------------
# Resolution helpers (push side)
# ---------------------------------------------------------------------------
def _resolve_feed_url(pc, pod):
    """Best-effort RSS feed URL for a subscribed podcast (cached)."""
    cached = _cache_get(_feed_to_uuid, "url:" + pod.uuid)
    if cached:
        return cached
    try:
        full = pc.get_podcast(pod.uuid)
        url = full.url or ""
    except Exception:
        url = ""
    _cache_put(_feed_to_uuid, "url:" + pod.uuid, url)
    return url


def _resolve_podcast_uuid(pc, feed_url, title=None):
    """Resolve an RSS feed URL to a Pocket Casts podcast UUID.

    Order: cache -> the user's subscriptions -> search by title (if provided).
    """
    if not feed_url:
        return None
    hit = _cache_get(_feed_to_uuid, feed_url)
    if hit:
        return hit

    # Try the user's own subscriptions first (cheap, exact url match).
    try:
        for pod in pc.get_subscribed_podcasts():
            full = pc.get_podcast(pod.uuid)
            if full.url == feed_url:
                _cache_put(_feed_to_uuid, feed_url, pod.uuid)
                return pod.uuid
    except Exception:
        pass

    # Fall back to catalog search by title, confirming via the resolved feed url.
    if title:
        try:
            for hit_pod in pc.search_podcasts(title)[:8]:
                full = pc.get_podcast(hit_pod.uuid)
                if full.url == feed_url:
                    _cache_put(_feed_to_uuid, feed_url, hit_pod.uuid)
                    return hit_pod.uuid
        except Exception:
            pass
    return None


def _resolve_episode_uuid(pc, podcast_uuid, enclosure_url):
    """Resolve an enclosure URL to a Pocket Casts episode UUID within a podcast."""
    if not enclosure_url:
        return None
    index = _cache_get(_podcast_episode_index, podcast_uuid)
    if index is None:
        pod = Podcast(podcast_uuid, pc)
        index = {e.url: e.uuid for e in pc.get_podcast_episodes(pod)}
        _cache_put(_podcast_episode_index, podcast_uuid, index)
    return index.get(enclosure_url)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8001")), debug=True)
