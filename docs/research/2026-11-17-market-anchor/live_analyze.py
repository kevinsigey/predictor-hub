"""Live paper-ledger market-anchor analysis (preregistered in ../2026-11-17-market-anchor.md section 1).

    python live_analyze.py <sites_root> <cfb_games_2026.parquet> > live_analyze.log
Builds exactly the Q1 population `paper.report` builds (same exclusions, same score_points), then scores the
model, the Kalshi price, and a chronological logit blend whose w for a game is fitted only on games with an
earlier kickoff day (w = 0 until 100 earlier games). Intervals resample games (hub `stats.cluster_ci`).
"""
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

HUB = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(HUB / "services" / "paper"))
from paper import report, sources, stats  # noqa: E402
from paper.ledger import Ledger, resolved, resolved_scores  # noqa: E402

from common import GRID, blend_p, ll, wilson  # noqa: E402

root = Path(sys.argv[1])
ledger = Ledger(str(HUB / "data" / "paper" / "ledger.jsonl"))
rows, scores = resolved(ledger), resolved_scores(ledger)
rows, scores, excluded = report.apply_exclusions(rows, scores, report.site_starts(root))
rows = [r for r in rows if not report._is_legacy_total(r)]
points = report.score_points(scores, rows)
starts = report.site_starts(root)
src = {(r.get("ticker"), r.get("logged_at") or ""): r for r in rows + scores}

P = pd.DataFrame(points)
P = P[P.sport.isin(["nfl", "nba", "cfb", "pl"])].copy()  # game sports; F1 has 3 races
P["gkey"] = P.sport + "|" + P.game_id.astype(str)
kick = []
for t, la, s, g in zip(P.ticker, P.logged_at, P.sport, P.game_id):
    r = src.get((t, la), {})
    st = sources.row_start(r, starts) if r else None
    kick.append(st)
P["kickoff"] = pd.to_datetime(kick, utc=True)
P["observed"] = pd.to_datetime([src.get((t, la), {}).get("price_observed_at") for t, la in zip(P.ticker, P.logged_at)], utc=True)
P["lead_h"] = (P.kickoff - P.observed).dt.total_seconds() / 3600
P["day"] = P.kickoff.dt.floor("D")
P = P.sort_values(["kickoff", "gkey"]).reset_index(drop=True)
print(f"Q1 points (game sports): {len(P)} rows, {P.gkey.nunique()} games; by sport {P.groupby('sport').gkey.nunique().to_dict()}")
print(f"excluded counts: { {k: sum(v.values()) for k, v in excluded.items()} }")

# chronological w: fitted on rows of games whose kickoff day is strictly earlier, game-weighted like Q1
y, pm, q = P.y.to_numpy(float), P.model_p.to_numpy(float), P.market_price.to_numpy(float)
clip = lambda x: np.clip(x, 0.01, 0.99)  # the hub's scoring clip
pm, q = clip(pm), clip(q)
gw = 1 / P.groupby("gkey").gkey.transform("size").to_numpy()
w_row = np.zeros(len(P))
fitted = {}
for d in sorted(P.day.dropna().unique()):
    earlier = (P.day < d).to_numpy()
    if P.gkey[earlier].nunique() < 100:
        continue
    losses = [np.average(ll(blend_p(q[earlier], pm[earlier], w), y[earlier]), weights=gw[earlier]) for w in GRID]
    w_row[(P.day == d).to_numpy()] = fitted[str(pd.Timestamp(d).date())] = float(GRID[int(np.argmin(losses))])
P["w"] = w_row
P["blend"] = clip(blend_p(q, pm, w_row))
print(f"chronological w: fitted on {len(fitted)} kickoff days covering {P.gkey[P.day.isin(pd.to_datetime(list(fitted)).tz_localize('UTC'))].nunique()} games; values {sorted(set(fitted.values()))}")


def q1(sub, cand):
    """Hub Q1 statistic: per-game mean of (market log loss - candidate log loss), games resampled."""
    g = defaultdict(list)
    for k, yy, c, m in zip(sub.gkey, sub.y, sub[cand], sub.market_price):
        g[k].append(stats.log_loss(m, yy) - stats.log_loss(c, yy))
    c = stats.cluster_ci(g, by_row=False)
    return (c["mean"], *c["ci"]) if c else (np.nan,) * 3


def q1_adj(sub, cand, alpha, iters=2000, seed=0):
    g = defaultdict(list)
    for k, yy, c, m in zip(sub.gkey, sub.y, sub[cand], sub.market_price):
        g[k].append(stats.log_loss(m, yy) - stats.log_loss(c, yy))
    v = np.array([np.mean(x) for x in g.values()])
    if len(v) < 2:
        return np.nan, np.nan
    d = v[np.random.default_rng(seed).integers(0, len(v), (iters, len(v)))].mean(1)
    return float(np.percentile(d, 100 * alpha / 2)), float(np.percentile(d, 100 * (1 - alpha / 2)))


fmt = lambda t: f"{t[0]:+.4f} [{t[1]:+.4f}, {t[2]:+.4f}]"
print(f"\n== OVERALL (positive = candidate better than the Kalshi price)")
print(f"pooled: model {fmt(q1(P, 'model_p'))}   chronological blend {fmt(q1(P, 'blend'))}   N games {P.gkey.nunique()}")
after = P[P.w > 0]
if len(after):
    print(f"  only games where w>0 (N={after.gkey.nunique()}): model {fmt(q1(after, 'model_p'))}  blend {fmt(q1(after, 'blend'))}")

# ---- preregistered segments
games = pd.read_parquet(sys.argv[2])
power = {"ACC", "Big Ten", "Big 12", "SEC"}
is_power = {str(g): ((hc in power) or ht == "Notre Dame") and ((ac in power) or at == "Notre Dame")
            for g, hc, ac, ht, at in zip(games.game_id, games.home_conference, games.away_conference, games.home_team, games.away_team)}
dis = (P.model_p - P.market_price).abs()
cells = [("L1 sport:market", f"{s}:{m}", (P.sport == s) & (P.market == m)) for s, m in sorted(set(zip(P.sport, P.market)))]
cells += [("L2 |model-price|", lab, mask) for lab, mask in (("<0.05", dis < 0.05), ("0.05-0.10", (dis >= 0.05) & (dis < 0.10)), (">=0.10", dis >= 0.10))]
cells += [("L3 lead time", lab, mask) for lab, mask in ((">=24h", P.lead_h >= 24), ("<24h", P.lead_h < 24))]
cfb_pow = P.game_id.astype(str).map(is_power)
cells += [("L4 CFB", lab, mask) for lab, mask in (("power v power", (P.sport == "cfb") & (cfb_pow == True)),  # noqa: E712
                                                   ("other", (P.sport == "cfb") & (cfb_pow == False)))]  # noqa: E712
K = len(cells)
a_adj = 0.05 / K
print(f"\n== SEGMENTS ({K} cells, Bonferroni alpha {a_adj:.4f}); gain = market log loss - candidate, per game")
out = []
for seg, lab, mask in cells:
    sub = P[np.asarray(mask, bool)]
    if sub.empty:
        continue
    gm, gb = q1(sub, "model_p"), q1(sub, "blend")
    adj = q1_adj(sub, "model_p", a_adj)
    side = sub[(sub.model_p - sub.market_price).abs() >= 0.05]
    hit = ((side.model_p > side.market_price) == (side.y == 1)).to_numpy()
    out.append(dict(segment=seg, cell=lab, games=sub.gkey.nunique(), rows=len(sub), gain_model=gm[0], lo=gm[1], hi=gm[2],
                    adj_lo=adj[0], adj_hi=adj[1], gain_blend=gb[0], blo=gb[1], bhi=gb[2],
                    side_n=len(side), side_hit=hit.mean() if len(side) else np.nan,
                    side_wilson=tuple(round(x, 3) for x in wilson(int(hit.sum()), len(side))) if len(side) else None,
                    flag=bool(adj[0] > 0)))
T = pd.DataFrame(out)
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 30)
print(T.round(4).to_string(index=False))
T.to_csv("live_segments.csv", index=False)
P.drop(columns=["day"]).to_csv("live_points.csv.gz", index=False)
print("\nflags:", T[T.flag][["segment", "cell"]].values.tolist() or "none")
