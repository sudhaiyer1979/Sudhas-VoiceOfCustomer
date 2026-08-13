#!/usr/bin/env python3
"""Collect a Steam game's official marketing copy via Steam's store API.

Prompts for a Steam store URL and a game name, extracts the App ID from
the URL, then fetches the official listing from Steam's public appdetails
endpoint (https://store.steampowered.com/api/appdetails). The short
description and the "about the game" copy are stripped of HTML tags,
images/video, section headers (navigation), and are never mixed with the
review-quote or system-requirements fields Steam also returns. What's
left is split into individual sentence/list-item claims, verbatim -- no
claim is invented, reworded, or summarized.

Marketing claims are written to data/marketing.json as an object:
    {
      "game_name": str,
      "source_url": str,
      "claims": [
        {"claim_id": "M001", "text": str},
        ...
      ]
    }

No mock or fabricated data is ever substituted. If Steam cannot be
reached, or returns an error, the script prints the exact error and
exits non-zero instead of writing partial/fake data.
"""

import json
import re
import sys
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

APPDETAILS_URL_TEMPLATE = "https://store.steampowered.com/api/appdetails?appids={appid}"
REQUEST_TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (compatible; SteamMarketingCollector/1.0)"

# Void/self-contained tags with no text children of their own -- just
# ignore their start tag, no need to track them as an open/close pair.
VOID_SKIP_TAGS = {"img", "source"}
# Tags whose *contents* are dropped entirely: video (no marketing text of
# its own, and Steam's <source> children inside it are never explicitly
# closed, so this must be paired on the stack rather than depth-counted),
# script/style, and headers (section navigation labels like "EXPLORE" /
# "BUILD", not claims). Paired via a stack so a stray unclosed tag inside
# (e.g. Steam's unclosed <source>) can never leave skip state stuck on.
CONTAINER_SKIP_TAGS = {"video", "script", "style", "h1", "h2", "h3", "h4", "h5", "h6"}
# Tags that separate one block of text from the next.
BLOCK_TAGS = {"p", "li", "br", "div", "tr"}

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_PATH = DATA_DIR / "marketing.json"


def extract_app_id(url: str) -> str:
    """Extract the numeric Steam App ID from a store.steampowered.com URL."""
    match = re.search(r"/app/(\d+)", url)
    if not match:
        raise ValueError(
            f"Could not find a Steam App ID in URL: {url!r}. "
            "Expected something like https://store.steampowered.com/app/252490/Rust/"
        )
    return match.group(1)


class _BlockTextExtractor(HTMLParser):
    """Turns Steam's store-description HTML into a list of text blocks.

    Each <p>/<li>/<br>/<div> boundary starts a new block. Content inside
    SKIP_CONTENT_TAGS (images, video, headers, script/style) is dropped.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = [""]
        self._skip_stack = []

    def handle_starttag(self, tag, attrs):
        if tag in VOID_SKIP_TAGS:
            return
        if tag in CONTAINER_SKIP_TAGS:
            self._skip_stack.append(tag)
            return
        if tag in BLOCK_TAGS and self.blocks[-1].strip():
            self.blocks.append("")

    def handle_startendtag(self, tag, attrs):
        # Self-closed tags (e.g. <br/>, <img/>) never carry text; nothing to skip-track.
        if tag in VOID_SKIP_TAGS or tag in CONTAINER_SKIP_TAGS:
            return
        if tag in BLOCK_TAGS and self.blocks[-1].strip():
            self.blocks.append("")

    def handle_endtag(self, tag):
        if self._skip_stack and self._skip_stack[-1] == tag:
            self._skip_stack.pop()
            return
        if self._skip_stack:
            # Inside a skipped container; ignore unrelated/unmatched end tags.
            return
        if tag in BLOCK_TAGS and self.blocks[-1].strip():
            self.blocks.append("")

    def handle_data(self, data):
        if self._skip_stack:
            return
        self.blocks[-1] += data


def html_to_blocks(raw_html: str):
    """Strip HTML/images/headers from a Steam description field, returning
    a list of clean text blocks (one per paragraph/list item)."""
    if not raw_html:
        return []
    parser = _BlockTextExtractor()
    parser.feed(raw_html)
    parser.close()
    blocks = []
    for block in parser.blocks:
        text = unescape(re.sub(r"\s+", " ", block)).strip()
        if text:
            blocks.append(text)
    return blocks


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'‘’“”])")


def split_into_claims(block: str):
    """Split one text block into individual sentence-level claims."""
    pieces = _SENTENCE_SPLIT_RE.split(block)
    return [p.strip() for p in pieces if p.strip()]


def is_meaningful_claim(text: str) -> bool:
    """Filter out leftover fragments that aren't real marketing claims
    (stray punctuation, bare URLs, single words from broken markup)."""
    if len(text) < 6:
        return False
    if not re.search(r"[A-Za-z]{3,}", text):
        return False
    if re.fullmatch(r"https?://\S+", text):
        return False
    return True


def fetch_appdetails(appid: str):
    """Fetch app details from Steam's appdetails API.

    Returns the parsed 'data' dict for this app. Raises RuntimeError with
    a clear message on any unrecoverable HTTP/network/API error.
    """
    url = APPDETAILS_URL_TEMPLATE.format(appid=appid)
    request = Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            status = response.getcode()
            if status != 200:
                raise RuntimeError(f"Steam returned unexpected HTTP status {status}")
            body = response.read()
            if not body:
                raise RuntimeError("Steam returned an empty response body")
    except HTTPError as e:
        raise RuntimeError(f"HTTP error {e.code} {e.reason} from Steam: {url}") from e
    except URLError as e:
        raise RuntimeError(f"Network error reaching Steam: {e.reason}") from e

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Steam returned malformed JSON: {e}") from e

    app_entry = payload.get(appid)
    if not app_entry:
        raise RuntimeError(f"Steam's response had no entry for app ID {appid}: {payload}")
    if not app_entry.get("success"):
        raise RuntimeError(
            f"Steam API reported failure for app ID {appid} "
            f"(the app may not exist or have no store page): {app_entry}"
        )
    data = app_entry.get("data")
    if not data:
        raise RuntimeError(f"Steam's response had no 'data' for app ID {appid}: {app_entry}")
    return data


def collect_claims(app_data: dict):
    """Extract marketing claims from short_description + about_the_game/
    detailed_description. Never touches app_data['reviews'] (critic-quote
    text, not the store's own copy) or the requirements fields."""
    raw_fields = [
        app_data.get("short_description", ""),
        app_data.get("about_the_game") or app_data.get("detailed_description", ""),
    ]

    claims = []
    seen = set()
    for raw_html in raw_fields:
        for block in html_to_blocks(raw_html):
            for sentence in split_into_claims(block):
                if not is_meaningful_claim(sentence):
                    continue
                key = sentence.lower()
                if key in seen:
                    continue
                seen.add(key)
                claims.append(sentence)

    return [
        {"claim_id": f"M{i + 1:03d}", "text": text}
        for i, text in enumerate(claims)
    ]


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
    print("Fetching official store description...\n")

    try:
        app_data = fetch_appdetails(appid)
    except RuntimeError as e:
        print(f"\nError: could not retrieve marketing copy from Steam.\n{e}", file=sys.stderr)
        sys.exit(1)

    claims = collect_claims(app_data)

    if not claims:
        print("\nError: zero marketing claims were extracted from Steam. Nothing was written.",
              file=sys.stderr)
        sys.exit(1)

    output = {
        "game_name": game_name,
        "source_url": steam_url,
        "claims": claims,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Saved {len(claims)} real marketing claims to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
