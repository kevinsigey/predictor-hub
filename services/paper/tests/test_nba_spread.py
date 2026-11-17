"""NBA spread: the rung is about a TEAM, and the ticker says which one.

Kalshi's NBA spread ladder is a MARGIN market, not a point spread: the rungs
are "San Antonio wins by over 8.5 points", "… over 9.5 points", and so on.
So a rung's probability is P(that team's margin > its own line), from the
model's predicted margin and the margin's sigma.

Two things make the rung's subject team hard to name:

  * the rung's TICKER SUFFIX carries the team's code with the line appended
    (`SAS9` for 8.5 points, `SAS8` for 7.5), so it is not a bare code;
  * the TITLE names the team the way Kalshi spells it ("San Antonio"), which
    is not how the NBA's own team reference spells it ("San Antonio Spurs").

The ticker suffix is therefore the reliable key: the code is the suffix minus
the line's own integer, and the code is what the NBA's schedule uses for that
game's two teams. This is checked against the committed line files rather than
assumed -- 1,896 NBA spread rows and 2,185 CFB spread rows, every one of them
`<code><int(line)+1>`.

The other half is the sigma. A spread needs P(margin > line), and that needs
the margin's OWN residual sigma; the total's sigma is a different quantity on a
different scale and cannot stand in for it. The pick feed publishes it (NBA
PR #146).
"""
from __future__ import annotations

import pytest
from conftest import config

from paper import sources
from paper.log import candidate_rows, spread_side

NBA_CFG = config(segments=[
    {"segment": "nba:moneyline", "sport": "nba", "market": "moneyline"},
    {"segment": "nba:spread", "sport": "nba", "market": "spread"},
    {"segment": "nba:total", "sport": "nba", "market": "total"},
])

MARGIN_SIGMA = 14.5


def nba_pick(*, game_id: str = "401812480", home: str = "HOU", away: str = "SAS",
             home_win: float = 0.55, margin: float = 12.0,
             generated_at: str = "2026-10-17T19:00:00+00:00",
             margin_sigma: float | None = MARGIN_SIGMA) -> dict:
    """One row of the NBA pick history, as the feed publishes it."""
    row = {
        "game_id": game_id,
        "game_date": "2026-10-23",
        "home_team": home,
        "away_team": away,
        "home_win_prob": home_win,
        "predicted_margin": margin,
        "predicted_total": 224.0,
        "total_sigma": 19.9,
        "model_version": "v1",
        "created_at": generated_at,
    }
    if margin_sigma is not None:
        row["margin_sigma"] = margin_sigma
    return row


def nba_games(pick: dict) -> dict[str, dict]:
    """The snapshot_games shape for one NBA pick."""
    home_win = pick.get("home_win_prob")
    return {
        pick["game_id"]: {
            "game_id": pick["game_id"],
            "sport": "nba",
            "generated_at": pick.get("created_at"),
            "model_fingerprint": pick.get("model_version"),
            "date": pick.get("game_date"),
            "start": sources.parse_ts("2026-10-24T02:00:00+00:00"),  # the site's tip_off
            "home": pick.get("home_team"),
            "away": pick.get("away_team"),
            "total_mu": pick.get("predicted_total"),
            "total_sigma": pick.get("total_sigma"),
            "margin_mu": pick.get("predicted_margin"),
            "margin_sigma": pick.get("margin_sigma"),
            "probs": {
                "home_win": home_win,
                "away_win": None if home_win is None else 1.0 - home_win,
                "over": None, "under": None,
                "home_cover": None, "away_cover": None,
            },
        }
    }


def spread_row(*, code: str = "SAS", line: float = 8.5, **over) -> dict:
    """One Kalshi NBA spread rung: `<code><int(line)+1>` in the suffix."""
    suffix = f"{code}{int(line) + 1}"
    row = {
        "snapshot_ts": "2026-10-17T20:00:00+00:00",
        "series": "KXNBASPREAD",
        "stat": "spread",
        "event_ticker": "KXNBASPREAD-26OCT23HOUSAS",
        "ticker": f"KXNBASPREAD-26OCT23HOUSAS-{suffix}",
        "title": "San Antonio wins by over 8.5 points",
        "player": None,
        "line": line,
        "yes_bid": 0.46, "yes_ask": 0.48,
        "no_bid": 0.50, "no_ask": 0.52,
        "last": 0.48, "volume": 100.0, "open_interest": 100.0,
        "status": "active", "result": None, "settlement_value": None,
        "occurrence": "2026-10-24T00:00:00+00:00",
        "close_time": "2026-10-26T00:00:00+00:00",
    }
    row.update(over)
    return row


def build_nba(rows, *, pick=None, cfg=None):
    pick = pick if pick is not None else nba_pick()
    return candidate_rows(
        list(rows),
        {"nba": nba_games(pick)},
        cfg if cfg is not None else NBA_CFG,
    )


def p_over_line(mu, sigma, line) -> float:
    import math
    return 0.5 * (1.0 - math.erf((line - mu) / sigma / math.sqrt(2.0)))


# --- the rung's subject team -------------------------------------------------

def test_the_rungs_subject_team_is_read_from_the_ticker_suffix():
    # The suffix is SAS9 for 8.5 points: the code, then the line's integer.
    row = spread_row(code="SAS", line=8.5)
    game = nba_games(nba_pick())["401812480"]
    assert spread_side(row, game) == "away"


def test_the_home_side_is_read_from_the_ticker_suffix_too():
    row = spread_row(code="HOU", line=8.5)
    game = nba_games(nba_pick())["401812480"]
    assert spread_side(row, game) == "home"


def test_a_rung_for_a_team_not_in_the_game_is_refused():
    row = spread_row(code="LAL", line=8.5)
    game = nba_games(nba_pick())["401812480"]
    assert spread_side(row, game) is None


# --- the price ---------------------------------------------------------------

def test_a_spread_rung_is_priced_from_the_margin_at_its_own_line():
    # The model has Houston by 12.0 with a margin sigma of 14.5, so "Houston
    # wins by over 8.5" is P(home margin > 8.5).
    made, _ = build_nba([spread_row(code="HOU", line=8.5, yes_ask=0.50, yes_bid=0.48)])
    assert [r["kind"] for r in made] == ["candidate"]
    expected = p_over_line(12.0, MARGIN_SIGMA, 8.5)
    assert made[0]["model_p"] == pytest.approx(expected, abs=1e-6)


def test_the_away_side_is_priced_from_the_away_margin():
    # The away team's margin is the NEGATIVE of the model's home margin, so a
    # rung about the away team must not be priced from the home margin.
    made, _ = build_nba([spread_row(code="SAS", line=8.5, yes_ask=0.10, yes_bid=0.08)])
    # P(away margin > 8.5) = P(home margin < -8.5) -- a long shot, so the
    # market's 10c price is not an edge and nothing is logged.
    assert made == []
    expected = p_over_line(-12.0, MARGIN_SIGMA, 8.5)
    assert expected < 0.20


def test_a_higher_rung_is_not_given_the_lower_rungs_probability():
    # "over 24.5 points" is much less likely than "over 8.5", and the model
    # knows that. Pricing both from one figure is the bug this avoids.
    made, skipped = build_nba([spread_row(code="HOU", line=24.5, yes_ask=0.20, yes_bid=0.18)])
    assert made == []
    assert skipped.get("below_edge_threshold") == 1


def test_a_spread_without_a_margin_sigma_is_not_priced():
    # No margin sigma is no price: a spread needs the margin's own sigma, and
    # the total's is a different quantity. Zero would be a claim that the
    # margin is certain.
    pick = nba_pick(margin_sigma=None)
    made, skipped = build_nba([spread_row(yes_ask=0.50, yes_bid=0.48)], pick=pick)
    assert made == []
    assert skipped.get("no_model_probability") == 1


def test_a_spread_without_a_line_is_not_priced():
    row = spread_row(yes_ask=0.50, yes_bid=0.48)
    row["line"] = None
    made, skipped = build_nba([row])
    assert made == []
    assert skipped.get("no_model_probability") == 1


def test_the_moneyline_is_unaffected_by_the_spread_wiring():
    # The spread work must not change how a moneyline is priced.
    row = {
        "snapshot_ts": "2026-10-17T20:00:00+00:00",
        "series": "KXNBAGAME", "stat": "moneyline",
        "event_ticker": "KXNBAGAME-26OCT23HOUSAS",
        "ticker": "KXNBAGAME-26OCT23HOUSAS-HOU",
        "title": "Houston wins", "player": None, "line": None,
        "yes_bid": 0.46, "yes_ask": 0.48, "no_bid": 0.52, "no_ask": 0.54,
        "last": 0.46, "volume": 100.0, "open_interest": 100.0,
        "status": "active", "result": None, "settlement_value": None,
        "occurrence": "2026-10-24T00:00:00+00:00",
        "close_time": "2026-10-26T00:00:00+00:00",
    }
    made, _ = build_nba([row])
    assert [r["kind"] for r in made] == ["candidate"]
    # The model's home win probability is 0.55, and this row is about the
    # home team.
    assert made[0]["model_p"] == pytest.approx(0.55, abs=1e-6)
    assert made[0]["segment"] == "nba:moneyline:0.50-0.60"
