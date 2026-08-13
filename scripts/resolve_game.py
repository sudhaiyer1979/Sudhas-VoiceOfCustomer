#!/usr/bin/env python3
"""Resolve a user-supplied game name (and optional Steam URL) to a
verified Steam App ID, canonical name, and canonical store URL.

Reusable, stdlib-only (+ `requests`) module used by app.py before running
any collection/analysis. Never scrapes non-Steam websites; only ever
talks to Steam's public store endpoints.

Resolution cases (see WS8 spec):

1. A Steam URL with a specific /app/<id> is given, plus a game name ->
   fetch the real name for that App ID and require it to closely match
   the given game name.
2. Only a game name is given (no URL, or a generic Steam domain with no
   specific app id) -> search Steam by name and take the best match.
3. A non-Steam URL is given -> ignored (never scraped); resolution falls
   back to a Steam name search, with an informational note surfaced to
   the caller.
4. A specific App ID's real name and the given game name don't match
   closely -> raise GameMismatchError; nothing is analyzed.

Raises GameNotFoundError or GameMismatchError (both subclasses of
ResolutionError) with a human-readable message on failure. Never
fabricates a result -- every returned app_id/name/url comes from a real
Steam API response.
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional
from urllib.parse import urlparse

import requests

APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
STORESEARCH_URL = "https://store.steampowered.com/api/storesearch/"
REQUEST_TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (compatible; SteamGameResolver/1.0)"
NAME_MATCH_THRESHOLD = 0.72

STEAM_DOMAIN_MARKERS = ("steampowered.com", "steamcommunity.com", "steam.com")

NON_STEAM_NOTE = "This version analyzes Steam store data and Steam player reviews."


class ResolutionError(Exception):
    """Base class for all game-resolution failures. .message is user-facing."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class GameNotFoundError(ResolutionError):
    pass


class GameMismatchError(ResolutionError):
    pass


class SteamUnavailableError(ResolutionError):
    pass


@dataclass
class ResolvedGame:
    game_name: str          # canonical name as reported by Steam
    app_id: str
    source_url: str         # canonical https://store.steampowered.com/app/<id> URL
    note: Optional[str] = None  # informational note to show the user, if any


def normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def names_match(a: str, b: str, threshold: float = NAME_MATCH_THRESHOLD) -> bool:
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if na in nb or nb in na:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= threshold


def extract_app_id(url: str) -> Optional[str]:
    """Return the App ID from a /app/<id> path, or None if not present."""
    match = re.search(r"/app/(\d+)", url)
    return match.group(1) if match else None


def is_steam_url(url: str) -> bool:
    lowered = url.lower()
    return any(marker in lowered for marker in STEAM_DOMAIN_MARKERS)


def looks_like_url(text: str) -> bool:
    if not text:
        return False
    parsed = urlparse(text if "//" in text else f"//{text}")
    return bool(parsed.netloc)


def fetch_app_name(app_id: str) -> str:
    """Fetch the canonical Steam store name for an App ID.

    Raises SteamUnavailableError on network/HTTP failure,
    GameNotFoundError if the App ID doesn't exist on Steam.
    """
    try:
        response = requests.get(
            APPDETAILS_URL,
            params={"appids": app_id},
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as e:
        raise SteamUnavailableError(f"Could not reach Steam to verify App ID {app_id}: {e}") from e
    except ValueError as e:
        raise SteamUnavailableError(f"Steam returned malformed JSON for App ID {app_id}: {e}") from e

    entry = payload.get(app_id)
    if not entry or not entry.get("success"):
        raise GameNotFoundError(
            f"Steam App ID {app_id} does not appear to exist (no store page found)."
        )
    data = entry.get("data") or {}
    name = data.get("name")
    if not name:
        raise GameNotFoundError(f"Steam App ID {app_id} has no name on its store page.")
    return name


def search_steam_by_name(game_name: str):
    """Search Steam's store for a game name. Returns a list of real
    {app_id, name} candidates ordered by Steam's own relevance ranking.
    Raises SteamUnavailableError on network/HTTP failure.
    """
    try:
        response = requests.get(
            STORESEARCH_URL,
            params={"term": game_name, "cc": "us", "l": "english"},
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as e:
        raise SteamUnavailableError(f"Could not reach Steam to search for {game_name!r}: {e}") from e
    except ValueError as e:
        raise SteamUnavailableError(f"Steam returned malformed JSON searching for {game_name!r}: {e}") from e

    items = payload.get("items", [])
    return [{"app_id": str(item["id"]), "name": item["name"]} for item in items if item.get("id")]


def resolve_by_name(game_name: str) -> ResolvedGame:
    candidates = search_steam_by_name(game_name)
    if not candidates:
        raise GameNotFoundError(
            f"Could not find {game_name!r} on Steam. Double-check the spelling, "
            "or provide the exact Steam store URL."
        )

    best = candidates[0]
    if not names_match(best["name"], game_name):
        raise GameNotFoundError(
            f"The closest Steam match for {game_name!r} was {best['name']!r}, "
            "which doesn't look like the same game. Double-check the spelling, "
            "or provide the exact Steam store URL."
        )

    return ResolvedGame(
        game_name=best["name"],
        app_id=best["app_id"],
        source_url=f"https://store.steampowered.com/app/{best['app_id']}",
    )


def resolve_game(game_name: str, steam_url: str = "") -> ResolvedGame:
    """Resolve and validate a game against Steam. See module docstring
    for the case-by-case behavior. Raises ResolutionError on failure."""
    game_name = (game_name or "").strip()
    steam_url = (steam_url or "").strip()

    if not game_name:
        raise ResolutionError("Game Name is required.")

    if not steam_url:
        # Case 2: name only.
        return resolve_by_name(game_name)

    app_id = extract_app_id(steam_url)

    if app_id:
        # Case 1: specific App ID given -- verify it matches the name.
        canonical_name = fetch_app_name(app_id)
        if not names_match(canonical_name, game_name):
            raise GameMismatchError(
                f"The Steam URL you provided is for {canonical_name!r} (App ID {app_id}), "
                f"but the game name you entered was {game_name!r}. "
                "These don't appear to be the same game, so nothing was analyzed. "
                "Please double-check the URL and game name."
            )
        return ResolvedGame(
            game_name=canonical_name,
            app_id=app_id,
            source_url=f"https://store.steampowered.com/app/{app_id}",
        )

    if is_steam_url(steam_url):
        # Case 3: generic Steam domain, no specific app -- resolve by name.
        return resolve_by_name(game_name)

    # Case 4: some other, non-Steam URL -- never scraped; fall back to a
    # Steam name search, with a note explaining why the URL was ignored.
    resolved = resolve_by_name(game_name)
    resolved.note = NON_STEAM_NOTE
    return resolved
