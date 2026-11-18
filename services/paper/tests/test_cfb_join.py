"""CFB: the Kalshi side and the model side have to be joined.

Two facts make this sport different from the others the ledger reads:

1. **The Kalshi event ticker carries team CODES, not names.** `KXNCAAFGAME-26OCT24BUFFMOH`
   means Buffalo at Miami (OH) — but the code lengths are not fixed (`BUFF` is
   four letters, `MOH` is three), so the code string cannot be split without
   knowing the two teams. The league's own codes are the only reliable key, and
   they are not in the snapshot: the snapshot's `home_team`/`away_team` are
   CFBD's names ("Buffalo", "Miami (OH)").

2. **The date is on the game, not on the ticker.** Kalshi's event ticker says
   the game is on the 24th; the snapshot's `gameday` is a UTC instant that for
   an evening kickoff is still the 24th in ET but is the 25th in UTC. So the
   two disagree by a day for exactly the late games.

The join therefore resolves the ticker to a game by the team CODES, taken from
the Kalshi market titles, and accepts a one-day date difference.

What is NOT done here is a fuzzy match on names. "Appalachian St." and
"App State" are the same school and the model calls it "App State"; "San José
State" is "San Jose St." on one side and "San José State" on the other. A
name-normalising join would silently pair the wrong games. The codes do not
have that problem, and 100% of the FBS-vs-FBS games Kalshi lists resolve
through them.
"""
from __future__ import annotations

import math

import pytest
from conftest import config, kalshi_row

CFB_CFG = config(segments=[
    {"segment": "cfb:moneyline", "sport": "cfb", "market": "moneyline"},
    {"segment": "cfb:total", "sport": "cfb", "market": "total"},
    {"segment": "cfb:spread", "sport": "cfb", "market": "spread"},
])

#: Kalshi's league codes for the two teams, and the codes the ledger reads the
#: event ticker's team string as when it cannot split it.
BUFFALO = "BUFF"
MIAMIOH = "MOH"


def cfb_snapshot(*, generated_at: str = "2026-10-17T19:00:00+00:00",
                 games=None, predictions=None) -> dict:
    """A CFB snapshot with one week of games and their predictions."""
    week = {
        "games": games if games is not None else [{
            "game_id": "401866448",
            "gameday": "2026-10-25T00:00:00+00:00",   # 8pm ET on the 24th
            "home_team": "Buffalo",
            "away_team": "Miami (OH)",
            "home_score": None,
            "away_score": None,
        }],
        "predictions": predictions if predictions is not None else {
            "401866448": {
                "home_win_prob": 0.62,
                "away_win_prob": 0.38,
                "predicted_margin": 7.5,
                "predicted_total": 51.0,
                "sigma": 14.0,
                "total_sigma": 12.0,
                "model_version": "ridge@2026-10-15",
            }
        },
    }
    return {"generated_at": generated_at, "season": 2026, "weeks": {"8": week}}


def cfb_row(*, suffix: str = BUFFALO, title: str = "Buffalo wins",
            line: float | None = None, stat: str = "moneyline",
            series: str = "KXNCAAFGAME", **over) -> dict:
    """One Kalshi CFB rung. The event ticker names both teams by code."""
    event = f"{series}-26OCT24{BUFFALO}{MIAMIOH}"
    return kalshi_row(
        series=series,
        stat=stat,
        line=line,
        event_ticker=event,
        ticker=f"{event}-{suffix}",
        title=title,
        player=None,
        occurrence="2026-10-25T00:00:00+00:00",
        **over,
    )


def build_cfb(rows, *, cfg=None, snapshot=None):
    from paper import sources
    from paper.log import candidate_rows
    snap = snapshot if snapshot is not None else cfb_snapshot()
    return candidate_rows(
        list(rows),
        {"cfb": sources.snapshot_games(snap, "cfb")},
        cfg if cfg is not None else CFB_CFG,
    )


# --- the join ---------------------------------------------------------------

def test_a_moneyline_rung_joins_its_game_and_gets_the_model_probability():
    made, _ = build_cfb([cfb_row(yes_ask=0.55, yes_bid=0.53)])
    assert [r["kind"] for r in made] == ["candidate"]
    assert made[0]["model_p"] == pytest.approx(0.62, abs=1e-6)
    assert made[0]["segment"] == "cfb:moneyline:0.60-0.70"
    assert made[0]["game_id"] == "401866448"


def test_the_away_side_is_priced_from_the_away_probability():
    # A game the model thinks the away side will win, priced below the model's
    # probability: the away side must get the AWAY probability, not the home's.
    snap = cfb_snapshot(predictions={"401866448": {
        "home_win_prob": 0.35, "away_win_prob": 0.65,
        "predicted_margin": -7.5, "predicted_total": 51.0,
        "sigma": 14.0, "total_sigma": 12.0, "model_version": "ridge@2026-10-15",
    }})
    row = cfb_row(suffix=MIAMIOH, title="Miami (OH) wins", yes_ask=0.55, yes_bid=0.53)
    made, _ = build_cfb([row], snapshot=snap)
    assert [r["kind"] for r in made] == ["candidate"]
    assert made[0]["model_p"] == pytest.approx(0.65, abs=1e-6)


def test_a_total_rung_is_priced_at_its_own_line():
    # The model's predicted total is 51.0 with a sigma of 12.0, so a 42.5 line
    # is likelier than not and a 62.5 line is not. Each is priced at its own
    # line, which is what a ladder needs.
    import math
    row = cfb_row(series="KXNCAAFTOTAL", stat="total", suffix="43", line=42.5,
                  title="Full Game: Over 42.5 points scored", yes_ask=0.55, yes_bid=0.53)
    made, _ = build_cfb([row])
    assert [r["kind"] for r in made] == ["candidate"]
    expected = 0.5 * (1.0 - math.erf((42.5 - 51.0) / 12.0 / math.sqrt(2.0)))
    assert made[0]["model_p"] == pytest.approx(expected, abs=1e-6)
    assert made[0]["model_p"] > 0.6


def test_a_high_total_rung_is_not_given_the_low_rungs_probability():
    # The old bug: every rung got one over_prob and "over 62.5" looked like a
    # huge edge at a 26c price.
    row = cfb_row(series="KXNCAAFTOTAL", stat="total", suffix="63", line=62.5,
                  title="Full Game: Over 62.5 points scored", yes_ask=0.20, yes_bid=0.18, no_bid=0.80, no_ask=0.82)
    made, skipped = build_cfb([row])
    assert made == []
    assert skipped.get("below_edge_threshold") == 1


def test_a_game_a_day_away_still_joins():
    # Kalshi says the 24th; the snapshot's UTC instant is the 25th. That is the
    # same game, and refusing it would drop half the slate.
    snap = cfb_snapshot()
    snap["weeks"]["8"]["games"][0]["gameday"] = "2026-10-25T12:00:00+00:00"
    made, _ = build_cfb([cfb_row(yes_ask=0.55, yes_bid=0.53)], snapshot=snap)
    assert len(made) == 1


def test_a_game_two_days_away_does_not_join():
    # The tolerance is one day. Two would start admitting a different game on
    # the same weekend.
    snap = cfb_snapshot()
    snap["weeks"]["8"]["games"][0]["gameday"] = "2026-10-26T12:00:00+00:00"
    made, skipped = build_cfb([cfb_row(yes_ask=0.55, yes_bid=0.53)], snapshot=snap)
    assert made == []
    assert skipped.get("no_matching_game") == 1


def test_a_game_several_days_away_does_not_join():
    snap = cfb_snapshot()
    snap["weeks"]["8"]["games"][0]["gameday"] = "2026-10-30T00:00:00+00:00"
    made, skipped = build_cfb([cfb_row(yes_ask=0.55, yes_bid=0.53)], snapshot=snap)
    assert made == []
    assert skipped.get("no_matching_game") == 1


def test_a_rung_about_a_team_that_is_not_in_the_game_is_not_logged():
    # The event ticker names a real game, but this rung's subject is a team
    # that is not in it. There is no probability for it, so there is no row.
    row = cfb_row(suffix="ZZZZ", title="Nowhere wins", yes_ask=0.55, yes_bid=0.53)
    made, skipped = build_cfb([row])
    assert made == []
    assert skipped.get("no_model_probability") == 1


def test_a_played_game_is_not_a_candidate():
    snap = cfb_snapshot()
    snap["weeks"]["8"]["games"][0]["home_score"] = 24.0
    made, skipped = build_cfb([cfb_row(yes_ask=0.55, yes_bid=0.53)], snapshot=snap)
    assert made == []


def test_a_game_with_no_prediction_has_no_probability():
    snap = cfb_snapshot(predictions={})
    made, skipped = build_cfb([cfb_row(yes_ask=0.55, yes_bid=0.53)], snapshot=snap)
    assert made == []
    assert skipped.get("no_matching_game") == 1


# --- spread -----------------------------------------------------------------

def spread_row(line: float, team: str = "Buffalo", **over) -> dict:
    """One Kalshi CFB spread rung: "<Team> wins by over N points"."""
    row = cfb_row(series="KXNCAAFSPREAD", stat="spread", suffix=f"BUFF{int(line)}",
                  line=line, title=f"{team} wins by over {line} points", **over)
    return row


def test_a_spread_rung_is_priced_from_the_margin_at_its_own_line():
    # Kalshi's spread ladder is a MARGIN market ("Buffalo wins by over 1.5
    # points"), so the probability is P(the team's margin > that line) from the
    # model's predicted margin (7.5) and its sigma (14.0) -- at the rung's own
    # line. A 1.5 line is the one the model likes; a 9.5 line it does not.
    row = spread_row(1.5, yes_ask=0.60, yes_bid=0.58)
    made, _ = build_cfb([row])
    assert [r["kind"] for r in made] == ["candidate"]
    expected = 0.5 * (1.0 - math.erf((1.5 - 7.5) / 14.0 / math.sqrt(2.0)))
    assert made[0]["model_p"] == pytest.approx(expected, abs=1e-6)
    assert made[0]["segment"] == "cfb:spread:0.60-0.70"


def test_a_higher_spread_rung_is_not_given_the_lower_rungs_probability():
    # "over 26.5 points" is much less likely than "over 1.5", and the model
    # knows that. Pricing both from one figure is the bug this avoids.
    made, skipped = build_cfb([spread_row(26.5, yes_ask=0.20, yes_bid=0.18, no_bid=0.80, no_ask=0.82)])
    # The guard, in both-sides terms (the logger has evaluated NO since
    # 2026-11-17): never a YES priced from the lower rung's figure. What
    # logs is the NO side, and its model_p is this rung's own small
    # probability (~0.087, against the 1.5 rung's ~0.26) -- the market
    # sells "not over 26.5" at 82c while the rung's own figure says it
    # is worth ~91c.
    assert [r["side"] for r in made] == ["no"]
    assert made[0]["model_p"] < 0.10


def test_the_away_side_spread_is_priced_against_the_away_margin():
    # The away team's margin is the NEGATIVE of the model's home margin, so a
    # rung about the away team must not be priced from the home margin. The
    # model has Buffalo by 7.5, so Miami (OH) winning by over 1.5 is a long
    # shot -- and the row is priced as one.
    row = spread_row(1.5, team="Miami (OH)", yes_ask=0.20, yes_bid=0.18, no_bid=0.80, no_ask=0.82)
    made, skipped = build_cfb([row])
    assert made == []
    assert skipped.get("below_edge_threshold") == 1
    # P(away margin > 1.5) = P(home margin < -1.5)
    expected = 0.5 * (1.0 + math.erf((-1.5 - 7.5) / 14.0 / math.sqrt(2.0)))
    assert expected < 0.35


def test_the_away_side_spread_is_logged_when_the_market_underprices_it():
    # The same rung, priced low enough to be an edge: the probability is the
    # AWAY team's, and it is the away team's margin that is used.
    row = spread_row(1.5, team="Miami (OH)", yes_ask=0.20, yes_bid=0.18, no_bid=0.80, no_ask=0.82)
    snap = cfb_snapshot(predictions={"401866448": {
        "home_win_prob": 0.30, "away_win_prob": 0.70,
        "predicted_margin": -7.5, "predicted_total": 51.0,
        "sigma": 14.0, "total_sigma": 12.0, "model_version": "ridge@2026-10-15",
    }})
    made, _ = build_cfb([row], snapshot=snap)
    assert [r["kind"] for r in made] == ["candidate"]
    expected = 0.5 * (1.0 + math.erf((-1.5 + 7.5) / 14.0 / math.sqrt(2.0)))
    assert made[0]["model_p"] == pytest.approx(expected, abs=1e-6)
    assert made[0]["model_p"] > 0.6


def test_a_spread_rung_about_a_team_not_in_the_game_is_not_logged():
    row = spread_row(9.5, team="Nowhere", yes_ask=0.60, yes_bid=0.58)
    made, skipped = build_cfb([row])
    assert made == []
    assert skipped.get("no_model_probability") == 1


def test_a_spread_rung_is_matched_on_kalshis_spelling_of_the_team():
    # Kalshi writes "Ohio St." where the model's snapshot writes "Ohio State".
    # The rung's title is Kalshi's spelling, so matching it against the
    # snapshot's spelling would refuse the rung over a spelling difference --
    # which is most of the spread ladder.
    snap = cfb_snapshot(
        games=[{
            "game_id": "401866449", "gameday": "2026-10-25T00:00:00+00:00",
            "home_team": "Ohio State", "away_team": "Eastern Michigan",
            "home_score": None, "away_score": None,
        }],
        predictions={"401866449": {
            "home_win_prob": 0.62, "away_win_prob": 0.38,
            "predicted_margin": 7.5, "predicted_total": 51.0,
            "sigma": 14.0, "total_sigma": 12.0, "model_version": "ridge@2026-10-15",
        }},
    )
    event = "KXNCAAFSPREAD-26OCT24OSUEMU"
    row = kalshi_row(
        series="KXNCAAFSPREAD", stat="spread", line=1.5,
        event_ticker=event, ticker=f"{event}-OSU1",
        title="Ohio St. wins by over 1.5 points", player=None,
        occurrence="2026-10-25T00:00:00+00:00", yes_ask=0.60, yes_bid=0.58,
    )
    made, skipped = build_cfb([row], snapshot=snap)
    assert [r["kind"] for r in made] == ["candidate"]
    assert made[0]["game_id"] == "401866449"
    assert made[0]["model_p"] == pytest.approx(
        0.5 * (1.0 - math.erf((1.5 - 7.5) / 14.0 / math.sqrt(2.0))), abs=1e-6)


def test_every_cfb_team_code_is_a_real_kalshi_market_code():
    # The table is the join's only key. A code that Kalshi does not use would
    # silently stop a team from ever joining, so every entry is checked against
    # the codes Kalshi publishes.
    from paper import sources

    assert len(sources.CFB_TEAM_CODES) >= 200
    for name, (code, kalshi_name) in sources.CFB_TEAM_CODES.items():
        assert code and code.isupper(), (name, code)
        assert kalshi_name and kalshi_name.strip() == kalshi_name, (name, kalshi_name)


def test_a_spread_rung_without_a_line_is_refused():
    row = spread_row(9.5, yes_ask=0.60, yes_bid=0.58)
    row["line"] = None
    made, skipped = build_cfb([row])
    assert made == []
    assert skipped.get("no_model_probability") == 1
