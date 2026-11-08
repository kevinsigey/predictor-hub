"""The arithmetic the report stands on: uncertainty, calibration, and closing line.

Four things live here, all pure functions over a list of settled ledger rows.

`max_drawdown` is a port of `max_drawdown` in `algo-trade-hub-prod`
(`tradehub/backtest/metrics.py:36`, read at commit 12432be) — peak-to-trough on
the cumulative curve, starting the peak at zero so a losing run from the start
registers. Copied rather than imported for the same reason the fee schedule was.

The bootstrap is a PAIRED bootstrap: one resample of row indices, applied to
every per-bet quantity at once, so the confidence interval on pnl keeps the
correlation between "how often we were right" and "what we paid". Resampling the
columns independently would let an interval be built out of combinations of bets
that never happened.

Closing-line value is the reason this report exists at all: it compares the price
we logged to the last price anybody could see before kickoff, so it is not
measuring luck. CLV is positive when we beat that closing price, which is the
part of an edge that is supposed to persist.
"""
from __future__ import annotations

import math
import random
from datetime import datetime
from statistics import fmean

BOOTSTRAP_ITERS = 4000
BOOTSTRAP_SEED = 20261017
CI_LEVEL = 0.95


def mean(values) -> float | None:
    values = list(values)
    return fmean(values) if values else None


def max_drawdown(pnls) -> float:
    """Largest peak-to-trough fall of the cumulative curve. Never negative."""
    peak = cum = worst = 0.0
    for pnl in pnls:
        cum += pnl
        peak = max(peak, cum)
        worst = max(worst, peak - cum)
    return worst


def bootstrap_mean_ci(values, *, iters: int = BOOTSTRAP_ITERS, seed: int = BOOTSTRAP_SEED,
                      level: float = CI_LEVEL) -> tuple[float, float] | None:
    """Percentile interval on the mean, resampling whole rows.

    Seeded, so the same ledger always prints the same interval. An unseeded CI in
    a report that gates on "does this interval exclude zero" is a coin flip that
    looks like evidence.
    """
    values = list(values)
    if not values:
        return None
    n = len(values)
    if n == 1:
        return values[0], values[0]
    rng = random.Random(seed)
    means = []
    for _ in range(iters):
        means.append(fmean(values[rng.randrange(n)] for _ in range(n)))
    means.sort()
    lo = means[int((1 - level) / 2 * (iters - 1))]
    hi = means[min(iters - 1, int(((1 + level) / 2) * (iters - 1)))]
    return lo, hi


def bootstrap_mean_ci_clustered(pairs, *, iters: int = BOOTSTRAP_ITERS, seed: int = BOOTSTRAP_SEED,
                               level: float = CI_LEVEL) -> tuple[float, float] | None:
    """Percentile interval on the mean, resampling GAMES rather than bets.

    `pairs` is a sequence of (game key, value). Each game's values are summed
    into one observation, and the games are what get resampled.

    A Kalshi ladder is many contracts on one game: a CFB game carries a
    moneyline, a spread ladder and a total ladder, and the live ledger holds
    239 CFB candidates from 44 games. Treating thirteen rungs of one game as
    thirteen independent observations makes the interval far too narrow -- the
    rungs of one game move together, because the game did. Resampling games
    cannot manufacture independence that the data does not have.

    The interval is on the mean pnl PER GAME, which is why the number is larger
    than the old per-bet interval: it is a different quantity, and the report
    says so.
    """
    totals: dict[object, float] = {}
    for key, value in pairs:
        totals[key] = totals.get(key, 0.0) + float(value)
    values = list(totals.values())
    if not values:
        return None
    n = len(values)
    if n == 1:
        return values[0], values[0]
    rng = random.Random(seed)
    means = []
    for _ in range(iters):
        means.append(fmean(values[rng.randrange(n)] for _ in range(n)))
    means.sort()
    lo = means[int((1 - level) / 2 * (iters - 1))]
    hi = means[min(iters - 1, int(((1 + level) / 2) * (iters - 1)))]
    return lo, hi


CLV_ITERS = 2000
CLV_SE_TARGET_CENTS = 1.0


def clv_summary(pairs) -> dict:
    """CLV uncertainty from (game key, clv cents) pairs.

    The interval resamples GAMES (the rungs of one game move together) and takes
    the per-bet mean of the resampled games' bets, 2000 draws, fixed seed.
    `clv_bets_to_resolve` is how many MORE bets the 95% half-width needs to fall
    below 1 cent at the observed per-bet SD (iid approximation); None under 10.
    """
    groups: dict[object, list[float]] = {}
    for key, v in pairs:
        groups.setdefault(key, []).append(float(v))
    n = sum(len(g) for g in groups.values())
    out = {"clv_ci95": None, "clv_bets_to_resolve": None}
    if not n:
        return out
    sums = [(sum(g), len(g)) for g in groups.values()]
    if len(sums) == 1:
        m = sums[0][0] / sums[0][1]
        out["clv_ci95"] = [round(m, 4), round(m, 4)]
    else:
        rng = random.Random(BOOTSTRAP_SEED)
        k = len(sums)
        means = []
        for _ in range(CLV_ITERS):
            picks = [sums[rng.randrange(k)] for _ in range(k)]
            means.append(sum(a for a, _ in picks) / sum(b for _, b in picks))
        means.sort()
        out["clv_ci95"] = [round(means[int(0.025 * (CLV_ITERS - 1))], 4),
                           round(means[int(0.975 * (CLV_ITERS - 1))], 4)]
    if n >= 10:
        vals = [v for g in groups.values() for v in g]
        m = fmean(vals)
        sd = (sum((v - m) ** 2 for v in vals) / (n - 1)) ** 0.5
        need = math.ceil((1.96 * sd / CLV_SE_TARGET_CENTS) ** 2 - 1e-9)
        out["clv_bets_to_resolve"] = max(0, need - n)
    return out


def excludes_zero(interval: tuple[float, float] | None) -> bool | None:
    """True when the interval is entirely on one side of zero."""
    if interval is None:
        return None
    return interval[0] > 0.0 or interval[1] < 0.0


def is_profitable(interval: tuple[float, float] | None) -> bool | None:
    """True when the whole interval sits ABOVE zero.

    The brief words the gate as "a CI on edge that excludes zero", and taken
    literally that is not sufficient: a segment that lost all 300 of its bets
    produces a tight interval well below zero, which also excludes zero, and
    would sail through a gate meant to certify an edge. An edge is a claim that
    the segment made money, so the interval has to exclude zero from BELOW. The
    strict-inequality form is deliberate — a lower bound sitting exactly on zero
    is not evidence of profit, it is an absence of evidence against.
    """
    if interval is None:
        return None
    return interval[0] > 0.0


def calibration_gap(model_ps, outcomes) -> float | None:
    """|mean model_p - hit rate|, in percentage points.

    Zero means the model said exactly the right thing on average. Signed would be
    more informative but is easy to misread, so the report carries the sign
    separately as mean_model_p minus hit_rate.
    """
    pairs = [(p, o) for p, o in zip(model_ps, outcomes) if p is not None and o is not None]
    if not pairs:
        return None
    predicted = fmean(p for p, _ in pairs)
    actual = fmean(float(o) for _, o in pairs)
    return abs(predicted - actual) * 100.0


def calibration_trend(rows, *, min_n: int) -> dict:
    """Is the calibration gap widening?

    Compares the first half of the settled rows with the second half. It returns
    a verdict only when there are enough rows to make the comparison mean
    something; below that it says `measurable: False` rather than reporting a
    trend computed from a handful of bets, because "not widening" on seven bets
    is indistinguishable from "we have not looked".
    """
    settled = [r for r in rows if r.get("outcome") in ("yes", "no")]
    n = len(settled)
    if n < min_n:
        return {"measurable": False, "n_settled": n, "min_n": min_n,
                "widening": None, "gap_early": None, "gap_late": None}
    half = n // 2
    def gap(part):
        return calibration_gap(
            [r["model_p"] for r in part],
            [1 if r["outcome"] == "yes" else 0 for r in part],
        )
    early, late = gap(settled[:half]), gap(settled[half:])
    return {
        "measurable": True,
        "n_settled": n,
        "min_n": min_n,
        "gap_early": early,
        "gap_late": late,
        "widening": bool(late is not None and early is not None and late > early),
    }


def closing_prices(rows: list[dict]) -> dict[str, dict]:
    """The last pre-kickoff buyable price per ticker, from the Kalshi snapshots.

    Built by walking every observed row for that ticker and keeping the one with
    the latest `snapshot_ts` that is still strictly before kickoff. The
    comparison is against `occurrence`, not `close_time`, because Kalshi keeps
    trading these markets after the game and a post-final price is not a closing
    line.

    Both sides' prices are kept. A NO row is closed against the NO price: a
    market's YES and NO prices are not the same number, and pricing a NO row
    against the YES side would report a CLV for a price nobody could buy.
    """
    best: dict[str, dict] = {}
    for row in rows:
        ticker = row.get("ticker")
        seen_at = row.get("_seen_at")
        start = row.get("_kickoff")
        if not isinstance(ticker, str) or seen_at is None or start is None:
            continue
        if seen_at >= start:
            continue  # observed at or after kickoff: not a closing line
        price = row.get("_price")
        no_price = row.get("_no_price")
        if price is None and no_price is None:
            continue
        held = best.get(ticker)
        if held is None or seen_at >= held["_seen_at"]:
            best[ticker] = row
    return best


def clv_points(candidate: dict, closing: dict[str, dict]) -> float | None:
    """Closing-line value in cents: how much better the logged price was than the
    last price anyone could see before kickoff.

    Positive means we beat the closing line — we logged 40c and the market ended
    at 45c — which is the part of an edge that should persist, because it says we
    got to the bet before the market did. Negative means the opposite: we paid
    more than the market's final price, so the information that made the bet
    interesting had already arrived and we were late to it. Positive pnl with
    negative CLV is luck.

    The close is taken on the row's OWN side: a NO row is closed against the NO
    price, because that is the price the NO side was trading at.

    A close observed BEFORE the row was logged is not a close. It says what the
    market thought before we got there, which is not the same question, so such a
    row has no CLV rather than a misleading one. Missing is None, never zero:
    a zero would claim we exactly matched the close, which is a different claim
    from "we do not know".
    """
    row = closing.get(candidate.get("ticker"))
    if row is None:
        return None
    side = candidate.get("side") or "yes"
    if side == "no":
        close_price = row.get("_no_price")
    else:
        close_price = row.get("_price")
    if close_price is None:
        return None
    # The close has to have been observed at or after our own entry, or it is
    # not evidence about the close.
    logged_at = _parse_observed(candidate.get("price_observed_at"))
    seen_at = row.get("_seen_at")
    if logged_at is not None and isinstance(seen_at, datetime) and seen_at < logged_at:
        return None
    market_price = candidate.get("market_price")
    if not isinstance(market_price, (int, float)):
        return None
    return round((float(close_price) - float(market_price)) * 100.0, 4)


def _parse_observed(value) -> datetime | None:
    """An ISO-8601 instant as an aware datetime, or None."""
    from . import sources
    return sources.parse_ts(value)


def annotate_observed(rows: list[dict]) -> list[dict]:
    """Pre-parse the timestamps `closing_prices` needs, without mutating the rows."""
    from . import sources
    out = []
    for row in rows:
        seen_at = sources.parse_ts(row.get("snapshot_ts"))
        copy = dict(row)
        copy["_seen_at"] = seen_at
        copy["_kickoff"] = sources.kickoff(row)
        copy["_price"] = sources.buyable_price(row, "yes")
        copy["_no_price"] = sources.buyable_price(row, "no")
        out.append(copy)
    return out
