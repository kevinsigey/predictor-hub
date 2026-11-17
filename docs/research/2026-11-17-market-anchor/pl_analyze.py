"""PL market-anchor analysis (preregistered in ../2026-11-17-market-anchor.md section 1).

    PYTHONPATH=<PL origin/main src> python pl_analyze.py <pl_frame.pkl> <pl_cols.json> > pl_analyze.log
The frame is `build_training_frame(load_training_data(default_completed_seasons()))` from PL origin/main, built
once and pickled. Folds and features copy `evaluate/walk_forward.prepare_folds` (min_train_seasons=3, fouls
columns excluded, fillna(0)) and production `ml_scoreline` defaults. Writes pl_oof.csv.gz and pl_segments.csv.
"""
import json
import sys

import numpy as np
import pandas as pd

from common import boot, walk_forward_w
from pl_predictor.models import ml_scoreline

df = pd.read_pickle(sys.argv[1])  # our own frame, pickled in this session from PL origin/main code.reset_index(drop=True)
cols = [c for c in json.load(open(sys.argv[2])) if "fouls" not in c]
seasons = sorted(df.season.unique())
parts = []
for i in range(3, len(seasons)):
    tr, va = df[df.season.isin(seasons[:i])], df[df.season == seasons[i]]
    hm, am = ml_scoreline.train_goal_regressors(tr[cols].fillna(0), tr.goals_home, tr.goals_away)
    grids = ml_scoreline.predict_grids_batch(hm, am, va[cols].fillna(0))
    r = va[["season", "date", "team_home", "team_away", "ftr", "psch", "pscd", "psca", "b365_h", "b365_d", "b365_a"]].copy()
    r[["p_h", "p_d", "p_a"]] = np.array([[g.home_win, g.draw, g.away_win] for g in grids])
    parts.append(r)
    print("fold", seasons[i], len(r), flush=True)
R = pd.concat(parts, ignore_index=True)
R["date"] = pd.to_datetime(R.date)
R = R.sort_values(["date", "team_home"]).reset_index(drop=True)
R.to_csv("pl_oof.csv.gz", index=False)

y = R.ftr.map({"H": 0, "D": 1, "A": 2}).to_numpy()
P = R[["p_h", "p_d", "p_a"]].to_numpy()
P = P / P.sum(1, keepdims=True)


def devig(cols_):
    o = 1 / R[cols_].to_numpy(float)
    return o / o.sum(1, keepdims=True)


Qp, Qb = devig(["psch", "pscd", "psca"]), devig(["b365_h", "b365_d", "b365_a"])
okp = ~np.isnan(Qp).any(1)


def pool(Q, w):
    L = (1 - w) * np.log(np.clip(Q, 1e-9, 1)) + w * np.log(np.clip(P, 1e-9, 1))
    E = np.exp(L - L.max(1, keepdims=True))
    return E / E.sum(1, keepdims=True)


def ll3(X):
    return -np.log(np.clip(X[np.arange(len(y)), y], 1e-9, 1))


def rps(X):
    O = np.eye(3)[y]
    return ((np.cumsum(X, 1)[:, :2] - np.cumsum(O, 1)[:, :2]) ** 2).sum(1) / 2


S = R.season.map({s: k for k, s in enumerate(seasons)}).to_numpy()
fmt = lambda t: f"{t[0]:+.4f} [{t[1]:+.4f}, {t[2]:+.4f}]"
print(f"\n== OVERALL N={len(R)} matches, validation {R.season.iloc[0]}..{R.season.iloc[-1]}")
for name, Q, ok in (("Pinnacle close", Qp, okp), ("Bet365 open", Qb, np.ones(len(R), bool))):
    Qf = np.where(ok[:, None], Q, 1 / 3)
    ws = walk_forward_w(S[ok], lambda m, w: ll3(pool(Qf, w))[ok][m].mean())
    wrow = np.zeros(len(R))
    wrow[ok] = [ws[s] for s in S[ok]]
    L =(1 - wrow[:, None]) * np.log(np.clip(Qf, 1e-9, 1)) + wrow[:, None] * np.log(np.clip(P, 1e-9, 1))
    B = np.exp(L - L.max(1, keepdims=True))
    B = B / B.sum(1, keepdims=True)
    print(f"{name}: N={ok.sum()}  walk-forward w by season {{{', '.join(f'{seasons[s]}: {w:.2f}' for s, w in ws.items())}}}")
    for mname, f in (("log loss", ll3), ("RPS", rps)):
        lm, lq, lb = f(P)[ok], f(Qf)[ok], f(B)[ok]
        print(f"  {mname}: model {lm.mean():.4f} market {lq.mean():.4f} blend {lb.mean():.4f}; market-model {fmt(boot(lq - lm))}; market-blend {fmt(boot(lq - lb))}")
    if name == "Pinnacle close":
        R["w_p"], Bp = wrow, B

# ---- preregistered segments vs Pinnacle close
long = pd.concat([R[["season", "date", "team_home"]].set_axis(["season", "date", "team"], axis=1).reset_index(),
                  R[["season", "date", "team_away"]].set_axis(["season", "date", "team"], axis=1).reset_index()])
long = long.sort_values("date")
long["n_before"] = long.groupby(["season", "team"]).cumcount()  # league matches already played this season
early = long.groupby("index").n_before.min() < 6  # either team has played <= 6 incl. this one
teams_by_season = df.groupby("season").apply(lambda d: set(d.team_home) | set(d.team_away))
prev = {s: teams_by_season[seasons[k - 1]] for k, s in enumerate(seasons) if k}
promoted = np.array([(h not in prev[s]) or (a not in prev[s]) for s, h, a in zip(R.season, R.team_home, R.team_away)])
dis = np.abs(P[:, 0] - Qp[:, 0])
cells = [("P1 early", "<=6 played", early.to_numpy()), ("P1 early", ">6 played", ~early.to_numpy()),
         ("P2 promoted", "yes", promoted), ("P2 promoted", "no", ~promoted),
         ("P3 |dP(home)|", "<0.05", dis < 0.05), ("P3 |dP(home)|", "0.05-0.10", (dis >= 0.05) & (dis < 0.10)),
         ("P3 |dP(home)|", ">=0.10", dis >= 0.10)]
K = len(cells)
a_adj = 0.05 / K
Qf = np.where(okp[:, None], Qp, 1 / 3)
rows = []
for seg, lab, m in cells:
    m = np.asarray(m, bool) & okp
    idx = np.where(m)[0]
    ws = walk_forward_w(S[m], lambda mm, w: ll3(pool(Qf, w))[idx][mm].mean())
    wseg = np.array([ws[s] for s in S[m]])
    L = (1 - wseg[:, None]) * np.log(np.clip(Qf[m], 1e-9, 1)) + wseg[:, None] * np.log(np.clip(P[m], 1e-9, 1))
    Bs = np.exp(L - L.max(1, keepdims=True))
    Bs = Bs / Bs.sum(1, keepdims=True)
    lq = ll3(Qf)[m]
    lbs = -np.log(np.clip(Bs[np.arange(len(idx)), y[m]], 1e-9, 1))
    gm, gs, gsa = boot(lq - ll3(P)[m]), boot(lq - lbs), boot(lq - lbs, alpha=a_adj)
    rows.append(dict(segment=seg, cell=lab, n=int(m.sum()), ll_model=ll3(P)[m].mean(), ll_market=lq.mean(),
                     gain_model=gm[0], gm_lo=gm[1], gm_hi=gm[2], gain_segblend=gs[0], gs_lo=gs[1], gs_hi=gs[2],
                     gs_adj_lo=gsa[1], gs_adj_hi=gsa[2], last_seg_w=float(wseg[-1]), flag=bool(gsa[1] > 0)))
T = pd.DataFrame(rows)
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 30)
print(f"\n== SEGMENTS vs Pinnacle close ({K} cells, Bonferroni alpha {a_adj:.4f}); gain = market log loss - candidate, positive = candidate better")
print(T.round(4).to_string(index=False))
T.to_csv("pl_segments.csv", index=False)
print("\nflags:", T[T.flag][["segment", "cell"]].values.tolist() or "none")
