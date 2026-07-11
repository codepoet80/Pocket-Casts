"""Quick smoke test: does this library work in 2026?

Public discover endpoints need no auth and run immediately.
Login is only attempted if POCKETCASTS_EMAIL / POCKETCASTS_PASSWORD are set.
"""
import os
import requests
import pocketcasts


def test_public():
    # Build an instance without triggering login (public endpoints need no auth).
    p = pocketcasts.Pocketcasts.__new__(pocketcasts.Pocketcasts)
    p._session = requests.Session()
    p._token = None

    charts = p.get_top_charts()
    print("get_top_charts ->", len(charts), "podcasts; #1:", charts[0].title)
    print("get_featured   ->", len(p.get_featured()), "podcasts")
    print("get_trending   ->", len(p.get_trending()), "podcasts")


def test_login():
    email = os.environ.get("POCKETCASTS_EMAIL")
    password = os.environ.get("POCKETCASTS_PASSWORD")
    if not (email and password):
        print("\n(skip login test — set POCKETCASTS_EMAIL / POCKETCASTS_PASSWORD to try it)")
        return
    p = pocketcasts.Pocketcasts(email, password)
    print("\nLogin OK. token[:12] =", p._token[:12], "...")


if __name__ == "__main__":
    test_public()
    test_login()
