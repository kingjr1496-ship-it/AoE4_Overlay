"""Detects skirmish (vs AI) and other local matches by reading the game's own
`warnings.log`. aoe4world only knows about online matches, so this is what
lets the overlay work in skirmish games.

The log lives in `Documents/My Games/Age of Empires IV/warnings.log` and is
rewritten every time the game starts. When a match starts it contains lines like:

    GAME -- Human Player: 0 KingShenkin 5272994 0 japanese
    GAME -- AI Player: 1 2 A.I. Hard -1 1 rus
    MapGen -- Generating with layout rolling_rivers
    MapGen -- Generating with biome taiga_woodlands_snow

Only files written by the game are read. Nothing touches game memory.
"""

import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from overlay.aoe4_data import civ_data
from overlay.logging_func import get_logger
from overlay.settings import settings

logger = get_logger(__name__)

LOG_LINE = re.compile(r"^\(\w\) \[(\d\d:\d\d:\d\d\.\d+)\] \[\d+\]: (.*)$")
HUMAN = re.compile(r"^GAME -- Human Player: (\d+) (.*) (-?\d+) (-?\d+) (\S+)$")
AI = re.compile(r"^GAME -- AI Player: (\d+) (-?\d+) (.*) (-?\d+) (-?\d+) (\S+)$")
MAPGEN = re.compile(r"^MapGen -- Generating with (layout|biome|size) (\S+)$")
WIN_CONDITION = re.compile(r"^GAME -- Win Condition Name: (\S+)")
PAUSE = re.compile(r"^GAME -- SimulationController::Pause ([01])$")
HEADER_TIME = re.compile(r"started at (\d{4}-\d\d-\d\d) (\d\d:\d\d)")

# Internal race names (as written in the log) -> civ names used by the overlay
# (these match the files in img/flags). Race names come from aoe4world's data
# (github.com/aoe4world/data, `attribName` in civilizations/civs-index.json);
# "japanese" and "rus" were also seen in a real skirmish log. Variants are
# written as "<base civ>_ha_<suffix>". Anything not listed falls back to a
# looser comparison in `civ_name`.
RACE_NAMES = {
    # Base game
    "abbasid": "Abbasid Dynasty",
    "chinese": "Chinese",
    "sultanate": "Delhi Sultanate",
    "english": "English",
    "french": "French",
    "hre": "Holy Roman Empire",
    "mongol": "Mongols",
    "rus": "Rus",
    # Free update
    "malian": "Malians",
    "ottoman": "Ottomans",
    # The Sultans Ascend
    "byzantine": "Byzantines",
    "japanese": "Japanese",
    "abbasid_ha_01": "Ayyubids",  # variant of Abbasid Dynasty
    "chinese_ha_01": "Zhu Xis Legacy",  # variant of Chinese
    "french_ha_01": "Jeanne Darc",  # variant of French
    "hre_ha_01": "Order Of The Dragon",  # variant of Holy Roman Empire
    # Knights of Cross and Rose
    "lancaster": "House of Lancaster",
    "templar": "Knights Templar",
    # Dynasties of the East (all variants)
    "mongol_ha_gol": "Golden Horde",  # variant of Mongols
    "byzantine_ha_mac": "Macedonian Dynasty",  # variant of Byzantines
    "japanese_ha_sen": "Sengoku Daimyo",  # variant of Japanese
    "sultanate_ha_tug": "Tughlaq Dynasty",  # variant of Delhi Sultanate
    # Yue Fei's Legacy
    "jin_dynasty": "Jin Dynasty",
    # Raiders of the North
    "scots": "Scots",
    "vikings": "Vikings",
}


def _simplify(text: str) -> str:
    text = re.sub(r"[^a-z]", "", text.lower())
    return text[:-1] if text.endswith("s") else text


def civ_name(race: str) -> str:
    """Converts a race name from the log into the overlay's civ name"""
    race = race.lower()
    if race in RACE_NAMES:
        return RACE_NAMES[race]
    simple = _simplify(race)
    for civ in civ_data.values():
        if simple and (simple == _simplify(civ) or simple in _simplify(civ)):
            return civ
    logger.warning(f"Unknown civilization in game log: {race}")
    return race.replace("_", " ").title()


def default_log_paths() -> List[str]:
    home = os.path.expanduser("~")
    rel = os.path.join("My Games", "Age of Empires IV", "warnings.log")
    candidates = [os.path.join(home, "Documents", rel)]
    onedrive = os.environ.get("OneDrive")
    if onedrive:
        candidates.append(os.path.join(onedrive, "Documents", rel))
    candidates.append(os.path.join(home, "OneDrive", "Documents", rel))
    return candidates


def find_log_file() -> Optional[str]:
    custom = getattr(settings, "aoe4_log_path", "")
    if custom:
        return custom if os.path.isfile(custom) else None
    for path in default_log_paths():
        if os.path.isfile(path):
            return path
    return None


def _empty_player(name: str, civ: str, team: int, rating: str) -> Dict[str, Any]:
    return {
        'civ': civ,
        'name': name,
        'team': team,
        'country': '',
        'rating': rating,
        'rank': '',
        'wins': '',
        'losses': '',
        'winrate': '',
        'civ_games': '',
        'civ_winrate': '',
        'civ_win_length_median': '',
    }


class MatchParser:
    """Reads log lines one by one and keeps track of the current match."""

    def __init__(self):
        self.date: Optional[str] = None
        self.reset_match()

    def reset_match(self):
        self.players: List[Dict[str, Any]] = []
        self.has_ai = False
        self.map_info: Dict[str, str] = {}
        self.win_condition = ""
        self.started_clock: Optional[str] = None
        self.collecting = False  # Between the player list and map generation
        self.in_match = False
        self.paused_at: Optional[float] = None
        self.paused_seconds = 0.0
        self.mission_started: Optional[float] = None

    def feed(self, raw: str) -> Optional[Dict[str, Any]]:
        """Feeds one line. Returns an event dict when something happens:
        `{"event": "start", ...}` once the match has loaded, or
        `{"event": "end", ...}` when it finishes."""
        raw = raw.rstrip("\r\n")
        header = HEADER_TIME.search(raw)
        if header and raw.startswith("RelicCardinal"):
            self.date = header.group(1)
            return None

        m = LOG_LINE.match(raw)
        if not m:
            return None
        clock, text = m.group(1), m.group(2)

        if text.startswith("GAME -- Scenario:"):
            self.reset_match()
            self.collecting = True
            self.started_clock = clock
            return None

        if not self.collecting and not self.in_match:
            return None

        if (w := WIN_CONDITION.match(text)):
            self.win_condition = w.group(1)
        elif (h := HUMAN.match(text)):
            self.players.append({
                "slot": int(h.group(1)),
                "name": h.group(2),
                "profile_id": int(h.group(3)),
                "team": int(h.group(4)),
                "civ": civ_name(h.group(5)),
                "ai": False,
            })
        elif (a := AI.match(text)):
            self.has_ai = True
            self.players.append({
                "slot": int(a.group(1)),
                "name": a.group(3),
                "profile_id": None,
                "team": int(a.group(5)),
                "civ": civ_name(a.group(6)),
                "ai": True,
            })
        elif (g := MAPGEN.match(text)):
            self.map_info[g.group(1)] = g.group(2)
        elif text.startswith("GAME -- Starting mission"):
            self.collecting = False
            self.in_match = True
            self.mission_started = _clock_seconds(clock)
            return {"event": "start", **self.match_data()}
        elif (p := PAUSE.match(text)) and self.in_match:
            now = _clock_seconds(clock)
            if p.group(1) == "1" and self.paused_at is None:
                self.paused_at = now
            elif p.group(1) == "0" and self.paused_at is not None:
                self.paused_seconds += _elapsed(self.paused_at, now)
                self.paused_at = None
        elif self.in_match and text.startswith("GameObj::ShutdownGameObj"):
            self.in_match = False
            data = {"event": "end", **self.match_data()}
            data["game_seconds"] = self.game_seconds(clock)
            return data
        return None

    def game_seconds(self, clock: str) -> Optional[float]:
        """In-game time at `clock`, with pauses taken out"""
        if self.mission_started is None:
            return None
        now = _clock_seconds(clock)
        paused = self.paused_seconds
        if self.paused_at is not None:
            paused += _elapsed(self.paused_at, now)
        return max(0.0, _elapsed(self.mission_started, now) - paused)

    def map_name(self) -> str:
        layout = self.map_info.get("layout", "")
        name = layout.replace("_", " ").title() if layout else "Skirmish"
        biome = self.map_info.get("biome")
        if biome:
            name += f" ({biome.replace('_', ' ').title()})"
        return name

    def match_data(self) -> Dict[str, Any]:
        """Match in the same shape as `helper_func.process_game` output"""
        main_team = None
        for p in self.players:
            if not p["ai"] and (p["profile_id"] == settings.profile_id
                                or p["name"] == settings.player_name):
                main_team = p["team"]
        if main_team is None:
            humans = [p for p in self.players if not p["ai"]]
            main_team = humans[0]["team"] if humans else 0

        players = sorted(self.players,
                         key=lambda p: (p["team"] != main_team, p["team"], p["slot"]))
        # The overlay uses team 1 for the main player's team and 2 for the rest
        team_ids = {}
        for p in players:
            team_ids.setdefault(p["team"], len(team_ids) + 1)

        result_players = []
        for p in players:
            rating = ""
            if p["ai"]:
                rating = p["name"].replace("A.I.", "").strip() or "AI"
            result_players.append(
                _empty_player(p["name"], p["civ"], team_ids[p["team"]], rating))

        started = None
        if self.date and self.started_clock:
            started = f"{self.date}T{self.started_clock[:8]}"
        return {
            'map': self.map_name(),
            'mode': 0,
            'started': started,
            'ranked': False,
            'server': 'local',
            'match_id': f"local-{started}",
            'local': True,
            'vs_ai': self.has_ai,
            'win_condition': self.win_condition,
            'players': result_players,
        }


def _clock_seconds(clock: str) -> float:
    h, m, s = clock.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _elapsed(start: float, end: float) -> float:
    """Seconds between two clock readings, allowing for midnight"""
    diff = end - start
    return diff + 86400 if diff < 0 else diff


class LocalLogChecker:
    """Same interface as `Api_checker`: `check_for_new_game` blocks until a
    local match starts or ends, then returns its data."""

    def __init__(self, poll_seconds: float = 2):
        self.force_stop = False
        self.poll_seconds = poll_seconds
        self.path: Optional[str] = None
        self.position = 0
        self.first_line = b""
        self.parser = MatchParser()
        self.skip_old = True  # Don't report matches already over when the app starts
        self._partial = b""
        self.pending: List[Dict[str, Any]] = []

    def check_for_new_game(self, delayed_seconds: int = 0) -> Optional[Dict[str, Any]]:
        end = time.time() + delayed_seconds
        while time.time() < end:
            if self.force_stop:
                return None
            time.sleep(0.5)
        while not self.force_stop:
            if not self.pending:
                self.read_new_lines()
            if self.pending:
                return self.pending.pop(0)
            time.sleep(self.poll_seconds)
        return None

    def _check_new_file(self, path: str):
        """Starts over when the game has begun a new log (it rewrites it on launch)"""
        try:
            with open(path, "rb") as f:
                first = f.readline()
            size = os.path.getsize(path)
        except OSError:
            return
        if path != self.path or first != self.first_line or size < self.position:
            self.path = path
            self.first_line = first
            self.position = 0
            self._partial = b""
            self.parser = MatchParser()

    def read_new_lines(self):
        """Reads lines added since the last call and queues any match events"""
        path = find_log_file()
        if path is None:
            return
        self._check_new_file(path)
        try:
            with open(path, "rb") as f:
                f.seek(self.position)
                chunk = f.read()
                self.position = f.tell()
        except OSError:
            logger.exception("Failed to read the game log")
            return

        lines = (self._partial + chunk).split(b"\n")
        self._partial = lines.pop()  # Last piece may be an unfinished line
        events = []
        for line in lines:
            event = self.parser.feed(line.decode("utf-8", errors="replace"))
            if event is not None:
                events.append(event)

        if self.skip_old:
            self.skip_old = False
            # On app start, only show a match that is still being played
            if events and events[-1]["event"] == "start" and self.parser.in_match:
                self.pending.append(events[-1])
            return
        self.pending.extend(events)
