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
import base64
import threading
from urllib.parse import urlparse, parse_qs

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
_podcast_details = {}        # podcast_uuid -> ((feed_url, {uuid: (title, enc, published)}), expires_at)


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

    {status:"ok", episodes:[{feedUrl, enclosureUrl, title, published, playingStatus, playedUpTo, starred}]}

    Goes per subscribed podcast: Pocket Casts only exposes an episode's played /
    in-progress state via user/podcast/episodes (which returns uuid + status +
    position but NOT title/url), so we cross-reference each podcast's full episode
    list to fill in enclosureUrl + title + published. This is the only way to get
    *played* episodes (they are absent from the in-progress/history/starred lists).
    """
    token = _extract_token()
    if not token:
        return _err("missing token")
    try:
        pc = _client_for_token(token)
        records = []
        for pod in pc.get_subscribed_podcasts():
            state = _episode_state(pc, pod.uuid)            # {uuid: (status, pos, starred)}
            if not state:
                continue
            feed_url, details = _podcast_detail_index(pc, pod.uuid)
            for uuid, (status, pos, starred) in state.items():
                d = details.get(uuid)
                if not d:
                    continue  # episode too old to be in the fetched list; skip
                title, enclosure, published = d
                records.append({
                    "feedUrl": feed_url,
                    "enclosureUrl": enclosure,
                    "title": title,
                    "published": published,
                    "playingStatus": status,
                    "playedUpTo": pos,
                    "starred": starred,
                })
    except Exception as e:
        return _err("pull failed: {}".format(e))
    return jsonify({"status": "ok", "episodes": records})


def _episode_state(pc, podcast_uuid):
    """Per-podcast episode state: {episode_uuid: (playingStatus, playedUpTo, starred)}.
    Only episodes the user has interacted with (played/in-progress/starred) appear."""
    r = pc._make_req("{}/user/podcast/episodes".format(pc.API),
                     method="JSON", data={"uuid": podcast_uuid})
    out = {}
    for e in r.json().get("episodes", []):
        try:
            pos = int(e.get("playedUpTo", 0) or 0)
        except (TypeError, ValueError):
            pos = 0
        try:
            status = int(e.get("playingStatus", 0) or 0)
        except (TypeError, ValueError):
            status = 0
        out[e["uuid"]] = (status, pos, bool(e.get("starred")))
    return out


def _podcast_detail_index(pc, podcast_uuid):
    """(feed_url, {episode_uuid: (title, enclosureUrl, publishedDate)}) from the cache
    host, cached. Supplies the title/url/date that user/podcast/episodes omits."""
    cached = _cache_get(_podcast_details, podcast_uuid)
    if cached:
        return cached
    raw = pc._make_req("{}/podcast/full/{}/0/3/1000".format(pc.CACHE, podcast_uuid),
                       method="GET").json().get("podcast", {})
    feed_url = raw.get("url", "")
    details = {}
    for e in raw.get("episodes", []):
        details[e["uuid"]] = (e.get("title", ""), e.get("url", ""),
                              (e.get("published", "") or "")[:10])
    value = (feed_url, details)
    _cache_put(_podcast_details, podcast_uuid, value)
    return value


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

    Body: {episodes:[{feedUrl, enclosureUrl, playingStatus, playedUpTo, title?, episodeTitle?}]}
      title        - the podcast (feed) title, used to resolve the podcast
      episodeTitle - the episode title, used as a fallback to resolve the episode
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
            episode_uuid = _resolve_episode_uuid(pc, podcast_uuid, enclosure,
                                                 item.get("episodeTitle"))
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

    # Recover the real RSS URL when the device sent a tiny.php proxy URL; this is
    # the reliable join key (Pocket Casts stores the real feed URL).
    real_feed = _decode_proxied_url(feed_url) or feed_url

    # Try the user's own subscriptions first: exact feed-url match (against the
    # real URL), else a tolerant title match (catalog titles differ).
    try:
        title_fallback = None
        for pod in pc.get_subscribed_podcasts():
            full = pc.get_podcast(pod.uuid)
            if full.url and full.url in (feed_url, real_feed):
                _cache_put(_feed_to_uuid, feed_url, pod.uuid)
                return pod.uuid
            if title and _title_matches(title, pod.title):
                title_fallback = pod.uuid
        if title_fallback:
            _cache_put(_feed_to_uuid, feed_url, title_fallback)
            return title_fallback
    except Exception:
        pass

    # Fall back to catalog search: prefer a real-feed-url match, else a tolerant
    # title match on the first result.
    if title:
        try:
            title_hit = None
            for hit_pod in pc.search_podcasts(title)[:8]:
                if not title_hit and _title_matches(title, hit_pod.title):
                    title_hit = hit_pod.uuid
                full = pc.get_podcast(hit_pod.uuid)
                if full.url and full.url in (feed_url, real_feed):
                    _cache_put(_feed_to_uuid, feed_url, hit_pod.uuid)
                    return hit_pod.uuid
            if title_hit:
                _cache_put(_feed_to_uuid, feed_url, title_hit)
                return title_hit
        except Exception:
            pass
    return None


def _decode_proxied_url(url):
    """Recover the original URL from a podcast-service proxy URL.

    drPodder subscribes to *tiny feeds*: podcast-service rewrites the RSS feed URL
    to `tiny.php?url=<base64>&...` and every enclosure to `mp3.php?<base64>`. Those
    proxy URLs never match Pocket Casts, which is why title matching was the only
    join key -- and titles differ between catalogs (drPodder "Up First" vs Pocket
    Casts "Up First from NPR"), so title matching fails.

    But the ORIGINAL url is embedded as base64 in the proxy URL. Decoding it gives
    back the real feed / enclosure URL, which DOES match Pocket Casts exactly. This
    restores the reliable URL-keyed join for pushes from tiny-feed devices.

    Returns the decoded URL, or None if `url` is not a decodable proxy URL.
    """
    if not url or "webosarchive.org" not in url:
        return None
    parsed = urlparse(url)
    if not any(parsed.path.endswith(p) for p in ("tiny.php", "mp3.php", "image.php")):
        return None
    qs = parse_qs(parsed.query)
    # tiny.php uses ?url=<b64>&max=...; mp3.php uses the bare query string as <b64>.
    if qs.get("url"):
        blob = qs["url"][0]
    else:
        blob = parsed.query.split("&", 1)[0]
    if not blob:
        return None
    blob = blob.replace(" ", "+")           # a '+' can arrive space-decoded
    blob += "=" * (-len(blob) % 4)          # restore stripped base64 padding
    try:
        decoded = base64.b64decode(blob).decode("utf-8", "replace")
    except Exception:
        return None
    return decoded if decoded.startswith("http") else None


def _title_matches(want, other):
    """Tolerant podcast/feed title match. Exact after normalization, else a prefix
    match so catalog suffixes differ gracefully ("up first" vs "up first from npr").
    Prefix (not substring) keeps false positives low; requires a non-trivial title."""
    if not want or not other:
        return False
    w, o = _norm_title(want), _norm_title(other)
    if w == o:
        return True
    if len(w) >= 4 and (o.startswith(w) or w.startswith(o)):
        return True
    return False


def _strip_query(url):
    return url.split("?", 1)[0] if url else url


def _norm_title(t):
    # Mirror the client's normTitle: unescape HTML entities, lowercase, collapse
    # whitespace. Title is the primary sync key for tiny (proxied) feeds.
    import html as _html
    return " ".join(_html.unescape(t or "").lower().split())


def _resolve_episode_uuid(pc, podcast_uuid, enclosure_url, episode_title=None):
    """Resolve a device episode to a Pocket Casts episode UUID within a podcast.

    Tries, in order: exact enclosure URL, query-stripped enclosure URL, then
    normalized episode title. The fallbacks absorb tracking-param drift and the
    rare URL difference between the device's feed and Pocket Casts."""
    index = _cache_get(_podcast_episode_index, podcast_uuid)
    if index is None:
        pod = Podcast(podcast_uuid, pc)
        by_url, by_stripped, by_title = {}, {}, {}
        for e in pc.get_podcast_episodes(pod):
            if e.url:
                by_url[e.url] = e.uuid
                by_stripped[_strip_query(e.url)] = e.uuid
            if e.title:
                by_title[_norm_title(e.title)] = e.uuid
        index = {"url": by_url, "stripped": by_stripped, "title": by_title}
        _cache_put(_podcast_episode_index, podcast_uuid, index)
    # Recover the real enclosure when the device sent an mp3.php proxy URL, then
    # try exact, query-stripped, and finally title matching.
    real_enc = _decode_proxied_url(enclosure_url) or enclosure_url
    for candidate in (enclosure_url, real_enc):
        if candidate and candidate in index["url"]:
            return index["url"][candidate]
    for candidate in (enclosure_url, real_enc):
        if candidate and _strip_query(candidate) in index["stripped"]:
            return index["stripped"][_strip_query(candidate)]
    if episode_title and _norm_title(episode_title) in index["title"]:
        return index["title"][_norm_title(episode_title)]
    return None


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8001")), debug=True)
