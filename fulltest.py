"""End-to-end exercise of every ported method against the live 2026 API.

Set credentials in the environment before running:
    POCKETCASTS_EMAIL=... POCKETCASTS_PASSWORD=... python3 fulltest.py
"""
import os
import pocketcasts

EMAIL = os.environ['POCKETCASTS_EMAIL']
PASSWORD = os.environ['POCKETCASTS_PASSWORD']
TAL = '3782b780-0bc5-012e-fb02-00163e1b201c'  # This American Life

p = pocketcasts.Pocketcasts(EMAIL, PASSWORD)
print('login OK, token present:', bool(p._token))

# --- public discover ---
print('top_charts:', len(p.get_top_charts()))
print('featured:  ', len(p.get_featured()))
print('trending:  ', len(p.get_trending()))

# --- search ---
found = p.search_podcasts('This American Life')
print('search:    ', len(found), '->', found[0].title if found else None)

# --- single podcast + episodes ---
pod = p.get_podcast(TAL)
print('get_podcast:', pod.title, '/', pod.author)
eps = p.get_podcast_episodes(pod)
print('episodes:  ', len(eps), '| newest:', eps[0].title, '@', eps[0].published_at)
print('  sort check newest>=oldest:', eps[0].published_at >= eps[-1].published_at)

# --- single episode + notes ---
one = p.get_episode(pod, eps[0].uuid)
print('get_episode:', one.title, '| dur', one.duration, '| type', one.file_type)
notes = p.get_episode_notes(one.uuid)
print('notes len: ', len(notes))

# --- subscribe / subscribed list / unsubscribe ---
p.subscribe_podcast(pod)
subs = p.get_subscribed_podcasts()
print('subscribed:', len(subs), '->', [s.title for s in subs])

# --- star / starred / unstar ---
p.update_starred(pod, one, 1)
starred = p.get_starred()
print('starred:   ', len(starred), '->', [e.title for e in starred])

# --- playing status + played position ---
p.update_playing_status(pod, one, pocketcasts.Episode.PlayingStatus.Playing)
one._playing_status = pocketcasts.Episode.PlayingStatus.Playing
p.update_played_position(pod, one, 42)
prog = p.get_in_progress()
print('in_progress:', len(prog), '->', [(e.title, e.played_up_to) for e in prog])

# --- new releases ---
print('new_releases:', len(p.get_new_releases()))

# --- cleanup ---
p.update_starred(pod, one, 0)
p.update_playing_status(pod, one, pocketcasts.Episode.PlayingStatus.Unplayed)
p.unsubscribe_podcast(pod)
print('cleanup done; subscribed now:', len(p.get_subscribed_podcasts()))
print('\nALL METHODS OK')
