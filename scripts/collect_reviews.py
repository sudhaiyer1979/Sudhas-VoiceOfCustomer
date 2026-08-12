#!/usr/bin/env python3
"""Collect up to 1,000 player reviews for a game from a given website URL."""

import argparse
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_PATH = DATA_DIR / "reviews.json"

USER_AGENT = "Mozilla/5.0 (compatible; VoiceOfCustomerBot/1.0)"
MAX_REVIEWS = 1000
PAGE_SIZE = 100


def get_inputs():
    parser = argparse.ArgumentParser(description="Collect player reviews for a game.")
    parser.add_argument("--url", help="Website URL to start from")
    parser.add_argument("--game", help="Game name")
    args = parser.parse_args()

    website_url = args.url or input("Website URL: ").strip()
    game_name = args.game or input("Game name: ").strip()
    if not website_url.startswith(("http://", "https://")):
        website_url = "https://" + website_url
    return website_url, game_name


def fetch_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def extract_steam_appid(url):
    match = re.search(r"steampowered\.com/app/(\d+)", url)
    return match.group(1) if match else None


def collect_steam_reviews(appid, max_reviews=MAX_REVIEWS):
    """Steam's review section is loaded dynamically via JS, so we pull the
    same data through Steam's public appreviews endpoint instead of parsing
    the rendered page."""
    reviews = []
    cursor = "*"
    seen_cursors = set()

    while len(reviews) < max_reviews:
        query = urllib.parse.urlencode(
            {
                "json": 1,
                "filter": "recent",
                "language": "english",
                "num_per_page": PAGE_SIZE,
                "cursor": cursor,
            }
        )
        url = f"https://store.steampowered.com/appreviews/{appid}?{query}"
        data = fetch_json(url)

        batch = data.get("reviews", [])
        if not batch:
            break

        for entry in batch:
            reviews.append(
                {
                    "text": entry.get("review", "").strip(),
                    "recommended": bool(entry.get("voted_up")),
                    "date": time.strftime(
                        "%Y-%m-%d", time.gmtime(entry.get("timestamp_created", 0))
                    ),
                }
            )
            if len(reviews) >= max_reviews:
                break

        next_cursor = data.get("cursor")
        if not next_cursor or next_cursor in seen_cursors:
            break
        seen_cursors.add(next_cursor)
        cursor = next_cursor
        time.sleep(0.5)

    return reviews[:max_reviews]


def collect_reviews(website_url, max_reviews=MAX_REVIEWS):
    appid = extract_steam_appid(website_url)
    if appid:
        return collect_steam_reviews(appid, max_reviews)
    raise NotImplementedError(f"No review collector implemented for this site: {website_url}")


def save_reviews(reviews):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(reviews, indent=2, ensure_ascii=False))
    return OUTPUT_PATH


def main():
    website_url, game_name = get_inputs()
    print(f"Collecting up to {MAX_REVIEWS} reviews for '{game_name}' from {website_url} ...")
    reviews = collect_reviews(website_url)
    out_path = save_reviews(reviews)
    print(f"Saved {len(reviews)} reviews to {out_path}")


if __name__ == "__main__":
    main()
