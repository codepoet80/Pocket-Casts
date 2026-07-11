"""Integration test for the sync service against the LIVE Pocket Casts API.

Run:
    POCKETCASTS_EMAIL=... POCKETCASTS_PASSWORD=... .venv/bin/python test_sync.py

Exercises login -> pull -> push round-trip -> subscriptions, then cleans up.
"""
import os
import sys
import json

import sync_service

EMAIL = os.environ["POCKETCASTS_EMAIL"]
PASSWORD = os.environ["POCKETCASTS_PASSWORD"]
FEED = "https://www.thisamericanlife.org/podcast/rss.xml"

c = sync_service.app.test_client()
fails = []


def check(name, cond):
    print(("  ok  " if cond else " FAIL ") + name)
    if not cond:
        fails.append(name)


# --- login ---
r = c.post("/sync/login", json={"email": EMAIL, "password": PASSWORD})
body = r.get_json()
check("login returns ok", body.get("status") == "ok")
token = body.get("token")
check("login returns a token", bool(token))

# --- subscriptions (subscribe first so there's data) ---
import pocketcasts
pc = pocketcasts.Pocketcasts(EMAIL, PASSWORD)
pod = pc.get_podcast("3782b780-0bc5-012e-fb02-00163e1b201c")  # This American Life
pc.subscribe_podcast(pod)
eps = pc.get_podcast_episodes(pod)
sample = eps[0]

r = c.get("/sync/subscriptions?token=" + token)
subs = r.get_json()
check("subscriptions ok", subs.get("status") == "ok")
check("subscription feedUrl resolved", any(f["feedUrl"] == FEED for f in subs["feeds"]))

# --- push: mark sample episode in-progress at 55s ---
r = c.post("/sync/push?token=" + token, json={"episodes": [{
    "feedUrl": FEED,
    "enclosureUrl": sample.url,
    "title": "This American Life",
    "playingStatus": 2,
    "playedUpTo": 55,
}]})
pushres = r.get_json()
check("push ok", pushres.get("status") == "ok")
check("push item succeeded", pushres["results"][0].get("ok") is True)
if not pushres["results"][0].get("ok"):
    print("     push error:", pushres["results"][0].get("error"))

# --- pull: the pushed state should come back, keyed by enclosure URL ---
r = c.get("/sync/pull?token=" + token)
pull = r.get_json()
check("pull ok", pull.get("status") == "ok")
match = next((e for e in pull["episodes"] if e["enclosureUrl"] == sample.url), None)
check("pulled episode present", match is not None)
if match:
    check("pulled position ~55", match["playedUpTo"] == 55)
    check("pulled status in-progress", match["playingStatus"] == 2)
    check("pulled feedUrl correct", match["feedUrl"] == FEED)

# --- cleanup ---
pc._make_req("{}/sync/update_episode".format(pc.API), method="JSON",
             data={"uuid": sample.uuid, "podcast": pod.uuid, "status": 0, "position": 0})
pc.unsubscribe_podcast(pod)

print()
if fails:
    print("FAILED:", ", ".join(fails))
    sys.exit(1)
print("ALL SYNC TESTS PASSED")
