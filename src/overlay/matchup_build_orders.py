"""Picks a build order for the current matchup (your civ vs the enemy civ).

1. A build order you saved yourself whose name (or text) mentions both civs,
   for example "Japanese vs Rus".
2. Otherwise the top-rated builds for your civ from aoe4guides.com, with the
   ones that mention the enemy civ moved to the front.

aoe4guides.com API: free, no key. Fair use asks for at most 30 list requests a
minute, honouring its cache time, and crediting/linking aoe4guides.com.
"""

import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from overlay.logging_func import CONFIG_FOLDER, get_logger

logger = get_logger(__name__)

API_URL = "https://aoe4-guides-api-7h2vti5ckq-ey.a.run.app/builds"
SITE_URL = "https://aoe4guides.com"
CACHE_FILE = os.path.join(CONFIG_FOLDER, "aoe4guides_cache.json")
CACHE_SECONDS = 6 * 3600  # Builds don't change often, keep requests low
FETCHED_PREFIX = "aoe4guides: "  # Names of fetched (temporary) build orders

# Overlay civ name -> (aoe4guides civ code, other names players use for it)
CIVS: Dict[str, Tuple[str, List[str]]] = {
    "Abbasid Dynasty": ("ABB", ["abbasid", "abba"]),
    "Ayyubids": ("AYY", ["ayyubid", "ayyu"]),
    "Byzantines": ("BYZ", ["byzantine", "byz"]),
    "Chinese": ("CHI", ["chinese"]),
    "Delhi Sultanate": ("DEL", ["delhi"]),
    "English": ("ENG", ["english", "eng"]),
    "French": ("FRE", ["french"]),
    "Golden Horde": ("GOH", ["golden horde", "goh"]),
    "Holy Roman Empire": ("HRE", ["hre", "holy roman"]),
    "House of Lancaster": ("HOL", ["lancaster"]),
    "Japanese": ("JAP", ["japanese", "japan", "jap"]),
    "Jeanne Darc": ("JDA", ["jeanne", "jda"]),
    "Jin Dynasty": ("JIN", ["jin"]),
    "Knights Templar": ("KTE", ["templar"]),
    "Macedonian Dynasty": ("MAC", ["macedonian"]),
    "Malians": ("MAL", ["malian", "mali"]),
    "Mongols": ("MON", ["mongol", "mongo"]),
    "Order Of The Dragon": ("DRA", ["order of the dragon", "ootd"]),
    "Ottomans": ("OTT", ["ottoman", "otto"]),
    "Rus": ("RUS", ["rus"]),
    "Scots": ("SCO", ["scots", "scot"]),
    "Sengoku Daimyo": ("SEN", ["sengoku"]),
    "Tughlaq Dynasty": ("TUG", ["tughlaq"]),
    "Vikings": ("VIK", ["viking"]),
    "Zhu Xis Legacy": ("ZXL", ["zhu xi", "zxl"]),
}


def _simplify(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def find_civ(name: str) -> Optional[str]:
    """Matches any spelling of a civ name ("Jeanne d'Arc", "Jeanne Darc",
    "Holy Roman Empire") to the key used in CIVS"""
    simple = _simplify(name)
    for civ in CIVS:
        if _simplify(civ) == simple:
            return civ
    return None


def mentions_civ(text: str, civ: str) -> bool:
    """True when `text` mentions `civ` by its name or a common short name"""
    civ = find_civ(civ) or civ
    names = [civ] + (CIVS[civ][1] if civ in CIVS else [])
    lower = text.lower()
    for name in names:
        # Whole words only, so "rus" doesn't match "rush"
        if re.search(r"(?<![a-z])" + re.escape(name.lower()) + r"s?(?![a-z])", lower):
            return True
    return False


def _build_civs(text: str) -> List[str]:
    """Civs a saved build order is for, read from its JSON (if it has one)"""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    civ = data.get("civilization")
    civs = civ if isinstance(civ, list) else [civ]
    return [c for c in (find_civ(str(x)) for x in civs if x) if c]


VS_SPLIT = re.compile(r"\bvs\b\.?|\bversus\b|\bagainst\b|\bv\b", re.IGNORECASE)


def pick_saved(build_orders: Dict[str, str], my_civ: str,
               enemy_civ: str) -> Optional[str]:
    """Name of your own saved build order for this matchup, if there is one.
    Best match: a name like "Japanese vs Rus". Next best: a build for your civ
    whose text mentions the enemy civ."""
    best, best_score = None, 0
    for name, text in build_orders.items():
        if name.startswith(FETCHED_PREFIX):
            continue
        civs = _build_civs(text)
        parts = VS_SPLIT.split(name, maxsplit=1)
        if len(parts) == 2:
            mine, theirs = parts
            for_me = mentions_civ(mine, my_civ) or (not mine.strip() and find_civ(my_civ) in civs)
            if for_me and mentions_civ(theirs, enemy_civ):
                score = 3
            else:
                continue
        else:
            for_me = mentions_civ(name, my_civ) or find_civ(my_civ) in civs
            if not for_me:
                continue
            if find_civ(enemy_civ) != find_civ(my_civ) and mentions_civ(name, enemy_civ):
                score = 2
            elif civs and mentions_civ(text, enemy_civ):
                score = 1
            else:
                continue
        if score > best_score:
            best, best_score = name, score
    return best


def rank_builds(builds: List[Dict[str, Any]], enemy_civ: str) -> List[Dict[str, Any]]:
    """Builds that mention the enemy civ first, otherwise keeps aoe4guides' order"""
    def mentions(build: Dict[str, Any]) -> bool:
        text = " ".join(str(build.get(k, "")) for k in ("name", "description", "strategy"))
        return mentions_civ(text, enemy_civ)

    return [b for b in builds if mentions(b)] + [b for b in builds if not mentions(b)]


def build_name(build: Dict[str, Any], enemy_civ: str) -> str:
    name = str(build.get("name", "Build order")).strip()
    author = str(build.get("author", "")).strip()
    tag = f" (vs {enemy_civ})" if mentions_civ(
        " ".join(str(build.get(k, "")) for k in ("name", "description", "strategy")),
        enemy_civ) else ""
    return f"{FETCHED_PREFIX}{name}{tag}" + (f" by {author}" if author else "")


def _read_cache() -> Dict[str, Any]:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_cache(cache: Dict[str, Any]):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except OSError:
        logger.exception("Failed to save the aoe4guides cache")


def fetch_builds(my_civ: str) -> List[Dict[str, Any]]:
    """Top-rated builds for `my_civ` from aoe4guides.com (cached for a few hours).
    Returns an empty list when offline or the civ is unknown."""
    civ = find_civ(my_civ)
    if civ is None:
        logger.warning(f"No aoe4guides civ code for {my_civ}")
        return []
    code = CIVS[civ][0]

    cache = _read_cache()
    entry = cache.get(code)
    if entry and time.time() - entry.get("time", 0) < CACHE_SECONDS:
        return entry.get("builds", [])

    try:
        resp = requests.get(API_URL,
                            params={"civ": code, "orderBy": "score", "overlay": "true"},
                            headers={"User-Agent": "AoE4_Overlay"},
                            timeout=10)
        resp.raise_for_status()
        builds = resp.json()
        if not isinstance(builds, list):
            raise ValueError(f"Unexpected aoe4guides response: {type(builds)}")
    except Exception:
        logger.exception(f"Failed to get build orders from aoe4guides for {code}")
        # Use old cached builds rather than nothing
        return entry.get("builds", []) if entry else []

    cache[code] = {"time": time.time(), "builds": builds}
    _write_cache(cache)
    return builds


def players_matchup(game_data: Dict[str, Any],
                    player_name: Optional[str]) -> Optional[Tuple[str, str]]:
    """(your civ, enemy civ) from overlay match data. The enemy is the first
    player on another team."""
    players = game_data.get("players", [])
    if len(players) < 2:
        return None
    me = next((p for p in players if player_name and p.get("name") == player_name),
              players[0])
    enemy = next((p for p in players if p.get("team") != me.get("team")), None)
    if enemy is None:
        return None
    return me["civ"], enemy["civ"]
