#!/usr/bin/env python3
"""Collect real player reviews for a Steam game via Steam's review API.

Prompts for a Steam store URL and a game name, extracts the App ID from
the URL, then paginates Steam's public appreviews endpoint
(https://store.steampowered.com/appreviews/{appid}) until either 1000
reviews are collected or Steam has no more reviews to return.

Reviews are written to data/reviews.json as an object:
    {
      "game_name": str,
      "source_url": str,
      "app_id": str,
      "review_count": int,
      "reviews": [
        {"review_id": str, "text": str, "recommended": bool, "date": "YYYY-MM-DD"}
      ]
    }

No mock or fabricated data is ever substituted. If Steam cannot be
reached, or returns an error, the script prints the exact error and
exits non-zero instead of writing partial/fake data.
"""

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

REVIEWS_URL_TEMPLATE = "https://store.steampowered.com/appreviews/{appid}"
MAX_REVIEWS = 1000
REVIEWS_PER_PAGE = 100
REQUEST_TIMEOUT = 15
MAX_RETRIES = 5
USER_AGENT = "Mozilla/5.0 (compatible; SteamReviewCollector/1.0)"

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_PATH = DATA_DIR / "reviews.json"


def extract_app_id(url: str) -> str:
    """Extract the numeric Steam App ID from a store.steampowered.com URL."""
    match = re.search(r"/app/(\d+)", url)
    if not match:
        raise ValueError(
            f"Could not find a Steam App ID in URL: {url!r}. "
            "Expected something like https://store.steampowered.com/app/252490/Rust/"
        )
    return match.group(1)


def fetch_page(appid: str, cursor: str):
    """Fetch a single page of reviews from Steam's appreviews API.

    Returns the parsed JSON dict. Raises RuntimeError with a clear
    message on unrecoverable HTTP/network errors, after retrying
    transient failures (e.g. rate limiting) with backoff.
    """
    params = (
        f"?json=1&filter=recent&language=all&review_type=all"
        f"&purchase_type=all&num_per_page={REVIEWS_PER_PAGE}"
        f"&cursor={quote(cursor)}"
    )
    url = REVIEWS_URL_TEMPLATE.format(appid=appid) + params
    request = Request(url, headers={"User-Agent": USER_AGENT})

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                status = response.getcode()
                if status != 200:
                    raise RuntimeError(f"Steam returned unexpected HTTP status {status}")
                body = response.read()
                if not body:
                    raise RuntimeError("Steam returned an empty response body")
                return json.loads(body)
        except HTTPError as e:
            if e.code == 429 or 500 <= e.code < 600:
                last_error = f"HTTP {e.code} {e.reason} from Steam"
                wait = 2 ** attempt
                print(f"  Warning: {last_error}; retrying in {wait}s "
                      f"(attempt {attempt}/{MAX_RETRIES})...", file=sys.stderr)
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP error {e.code} {e.reason} from Steam: {url}") from e
        except URLError as e:
            last_error = f"Network error reaching Steam: {e.reason}"
            wait = 2 ** attempt
            print(f"  Warning: {last_error}; retrying in {wait}s "
                  f"(attempt {attempt}/{MAX_RETRIES})...", file=sys.stderr)
            time.sleep(wait)
            continue
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Steam returned malformed JSON: {e}") from e

    raise RuntimeError(
        f"Failed to reach Steam after {MAX_RETRIES} attempts. Last error: {last_error}"
    )


def collect_reviews(appid: str, max_reviews: int = MAX_REVIEWS):
    reviews = []
    cursor = "*"
    seen_cursors = set()
    page = 0

    while len(reviews) < max_reviews:
        page += 1
        print(f"Fetching page {page} (collected {len(reviews)}/{max_reviews})...")
        data = fetch_page(appid, cursor)

        if data.get("success") != 1:
            raise RuntimeError(
                f"Steam API reported failure (success={data.get('success')}): {data}"
            )

        raw_reviews = data.get("reviews", [])
        if not raw_reviews:
            print("No more reviews returned by Steam; stopping.")
            break

        for r in raw_reviews:
            timestamp = r.get("timestamp_created")
            date_str = (
                datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
                if timestamp is not None
                else None
            )
            review_id = r.get("recommendationid") or str(len(reviews) + 1)
            reviews.append({
                "review_id": str(review_id),
                "text": r.get("review", ""),
                "recommended": bool(r.get("voted_up")),
                "date": date_str,
            })
            if len(reviews) >= max_reviews:
                break

        next_cursor = data.get("cursor")
        if not next_cursor or next_cursor == cursor or next_cursor in seen_cursors:
            print("Reached end of available reviews (cursor did not advance); stopping.")
            break
        seen_cursors.add(next_cursor)
        cursor = next_cursor

        # Be polite to Steam's servers between pages.
        time.sleep(1)

    return reviews[:max_reviews]


def main():
    steam_url = input("Enter the Steam game URL: ").strip()
    game_name = input("Enter the game name: ").strip()

    try:
        appid = extract_app_id(steam_url)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\nGame: {game_name}")
    print(f"Steam App ID: {appid}")
    print(f"Collecting up to {MAX_REVIEWS} reviews...\n")

    try:
        reviews = collect_reviews(appid, MAX_REVIEWS)
    except RuntimeError as e:
        print(f"\nError: could not collect reviews from Steam.\n{e}", file=sys.stderr)
        sys.exit(1)

    if not reviews:
        print("\nError: zero reviews were collected from Steam. Nothing was written.",
              file=sys.stderr)
        sys.exit(1)

    output = {
        "game_name": game_name,
        "source_url": steam_url,
        "app_id": appid,
        "review_count": len(reviews),
        "reviews": reviews,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nSaved {len(reviews)} real reviews to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
