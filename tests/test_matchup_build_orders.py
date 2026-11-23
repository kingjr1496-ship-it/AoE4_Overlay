import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overlay import matchup_build_orders as mbo  # noqa: E402
from overlay.build_order_tools import civilization_flags  # noqa: E402

FLAGS = os.path.join(os.path.dirname(__file__), "..", "src", "img", "flags")


def make_build(name, civ="Japanese", description="", strategy="", author="someone"):
    return {
        "name": name, "author": author, "civilization": civ,
        "description": description, "strategy": strategy, "source": "https://aoe4guides.com/builds/x",
        "build_order": [{
            "age": 1, "population_count": 6, "villager_count": 6, "time": "0:00",
            "resources": {"food": 6, "wood": 0, "gold": 0, "stone": 0},
            "notes": ["Villagers on @resource/sheep.webp@"],
        }],
    }


def test_every_civ_has_a_code_and_flag():
    flag_names = {f[:-5] for f in os.listdir(FLAGS) if f.endswith(".webp")}
    assert set(mbo.CIVS) == flag_names
    codes = [code for code, _ in mbo.CIVS.values()]
    assert len(codes) == len(set(codes)) == 25
    # Names used in build order files map to the same civs
    for civ in civilization_flags:
        assert mbo.find_civ(civ) is not None, civ


def test_mentions_civ_whole_words():
    assert mbo.mentions_civ("Good vs Rus and Mongols", "Rus")
    assert not mbo.mentions_civ("Fast rush into castle", "Rus")
    assert mbo.mentions_civ("anti-HRE timing", "Holy Roman Empire")
    assert mbo.mentions_civ("Strong into Jeanne d'Arc", "Jeanne Darc")
    assert mbo.mentions_civ("beats mongols", "Mongols")


def test_pick_saved_prefers_named_matchup():
    saved = {
        "Instructions": "Write your own build order.",
        "Japanese fast castle": json.dumps(make_build("FC")),
        "Japanese vs Rus": "plain text build",
        "English vs Rus": "plain text build",
        mbo.FETCHED_PREFIX + "Japanese vs Rus": "ignored",
    }
    assert mbo.pick_saved(saved, "Japanese", "Rus") == "Japanese vs Rus"
    assert mbo.pick_saved(saved, "Japanese", "French") is None
    assert mbo.pick_saved(saved, "English", "Rus") == "English vs Rus"


def test_pick_saved_uses_build_text():
    saved = {"My FC": json.dumps(make_build("FC", description="Great against Rus"))}
    assert mbo.pick_saved(saved, "Japanese", "Rus") == "My FC"
    assert mbo.pick_saved(saved, "Japanese", "French") is None


def test_pick_saved_mirror():
    assert mbo.pick_saved({"Rus vs Rus": "x", "Rus FC": "y"}, "Rus", "Rus") == "Rus vs Rus"


def test_rank_and_name():
    builds = [make_build("Fast castle"), make_build("Anti Rus rush", description="vs Rus"),
              make_build("Boom")]
    ranked = mbo.rank_builds(builds, "Rus")
    assert [b["name"] for b in ranked] == ["Anti Rus rush", "Fast castle", "Boom"]
    assert mbo.build_name(ranked[0], "Rus") == "aoe4guides: Anti Rus rush (vs Rus) by someone"
    assert mbo.build_name(ranked[1], "Rus") == "aoe4guides: Fast castle by someone"


def test_players_matchup():
    data = {"players": [
        {"name": "A.I. Hard", "civ": "Rus", "team": 2},
        {"name": "Me", "civ": "Japanese", "team": 1},
    ]}
    assert mbo.players_matchup(data, "Me") == ("Japanese", "Rus")
    assert mbo.players_matchup({"players": [{"name": "Me", "civ": "Rus", "team": 1}]}, "Me") is None


def test_fetch_uses_cache_and_survives_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(mbo, "CACHE_FILE", str(tmp_path / "cache.json"))
    calls = []

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return [make_build("FC")]

    def fake_get(url, params, headers, timeout):
        calls.append(params)
        return Resp()

    monkeypatch.setattr(mbo.requests, "get", fake_get)
    assert mbo.fetch_builds("Japanese")[0]["name"] == "FC"
    assert mbo.fetch_builds("Japanese")[0]["name"] == "FC"
    assert calls == [{"civ": "JAP", "orderBy": "score", "overlay": "true"}]  # second call cached

    def failing_get(*a, **k):
        raise OSError("offline")

    monkeypatch.setattr(mbo.requests, "get", failing_get)
    monkeypatch.setattr(mbo, "CACHE_SECONDS", -1)  # cache expired
    assert mbo.fetch_builds("Japanese")[0]["name"] == "FC"  # falls back to old cache
    assert mbo.fetch_builds("Rus") == []
