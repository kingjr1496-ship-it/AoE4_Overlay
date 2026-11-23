import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overlay import local_log_checking as llc  # noqa: E402
from overlay.settings import settings  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "data", "skirmish_warnings.log")


def parse_all(path):
    parser = llc.MatchParser()
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            e = parser.feed(line)
            if e:
                events.append(e)
    return events


def test_skirmish_start_and_end():
    settings.profile_id = 1234567
    events = parse_all(DATA)
    assert [e["event"] for e in events] == ["start", "end"]
    start = events[0]
    assert start["local"] and start["vs_ai"]
    assert start["map"] == "Rolling Rivers (Taiga Woodlands Snow)"
    assert start["started"] == "2026-11-23T16:27:10"
    me, ai = start["players"]
    assert (me["name"], me["civ"], me["team"]) == ("TestPlayer", "Japanese", 1)
    assert (ai["name"], ai["civ"], ai["team"], ai["rating"]) == ("A.I. Hard", "Rus", 2, "Hard")
    # 16:27:29.5 -> 16:55:49.9 minus ~1.8 min of pauses (and still paused at the end)
    assert 1590 < events[1]["game_seconds"] < 1610


def test_every_civ_has_a_flag():
    flags_dir = os.path.join(os.path.dirname(__file__), "..", "src", "img", "flags")
    flags = {f[:-5] for f in os.listdir(flags_dir) if f.endswith(".webp")}
    names = set(llc.RACE_NAMES.values())
    assert names <= flags, names - flags
    # Every civ the overlay knows about can be detected
    assert set(llc.civ_data.values()) <= names


def test_variant_and_fallback_names():
    assert llc.civ_name("mongol_ha_gol") == "Golden Horde"
    assert llc.civ_name("byzantine_ha_mac") == "Macedonian Dynasty"
    assert llc.civ_name("JAPANESE") == "Japanese"
    # Unknown names still produce something readable
    assert llc.civ_name("some_new_civ") == "Some New Civ"


def test_checker_tails_file(tmp_path, monkeypatch):
    log = tmp_path / "warnings.log"
    lines = open(DATA, "rb").read().split(b"\n")
    start_idx = next(i for i, l in enumerate(lines) if b"Starting mission" in l)
    log.write_bytes(b"\n".join(lines[:start_idx]) + b"\n")
    monkeypatch.setattr(settings, "aoe4_log_path", str(log), raising=False)

    checker = llc.LocalLogChecker(poll_seconds=0)
    checker.read_new_lines()  # app start: no match running yet
    assert checker.pending == []

    with open(log, "ab") as f:
        f.write(b"\n".join(lines[start_idx:]))
    checker.read_new_lines()
    assert [e["event"] for e in checker.pending] == ["start", "end"]


def test_app_started_mid_match(tmp_path, monkeypatch):
    log = tmp_path / "warnings.log"
    lines = open(DATA, "rb").read().split(b"\n")
    start_idx = next(i for i, l in enumerate(lines) if b"Starting mission" in l)
    log.write_bytes(b"\n".join(lines[:start_idx + 3]) + b"\n")
    monkeypatch.setattr(settings, "aoe4_log_path", str(log), raising=False)
    checker = llc.LocalLogChecker(poll_seconds=0)
    assert checker.check_for_new_game()["event"] == "start"


def test_old_finished_match_is_skipped(tmp_path, monkeypatch):
    log = tmp_path / "warnings.log"
    shutil.copy(DATA, log)
    monkeypatch.setattr(settings, "aoe4_log_path", str(log), raising=False)
    checker = llc.LocalLogChecker(poll_seconds=0)
    checker.read_new_lines()
    assert checker.pending == []
