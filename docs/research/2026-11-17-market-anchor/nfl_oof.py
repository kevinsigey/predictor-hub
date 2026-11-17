"""NFL: out-of-fold production-model predictions (origin/main ridge, default features) beside the closing line.

Run from an NFL_Predictor checkout (for data/cache) with PYTHONPATH=<origin/main src>:
    python nfl_oof.py <out.csv.gz>
Writes one row per validation game: model margin/total/home-win prob, spread/total line, line sigma, outcome,
starting QBs (pbp_agg.qb_games) and head coaches (nflverse schedules, 2017+). No fitting here sees its own season.
"""
import glob
import sys

import numpy as np
import pandas as pd

from nfl_predictor.evaluate import walk_forward as wf
from nfl_predictor.models import game_outcome as go
from nfl_predictor.tools import block_eval as be

g, aux = be.load_recent_nfl_data(2000, 2026)
parts = []
for f in wf.prepare_folds(g, aux=aux):
    tr, va, fc = f["train_df"], f["val_df"], f["feature_cols"]
    m, sigma = wf._predict_margins("ridge", tr, va, fc)
    t = wf._fit_predict("ridge", tr, va, fc, "total_points")
    trl = tr.dropna(subset=["spread_line"])
    out = va[["game_id", "season", "week", "home_team", "away_team", "margin", "total_points",
              "spread_line", "total_line"]].copy()
    out["model_margin"], out["model_total"] = m, t
    out["model_p"] = [go.margin_to_probabilities(x, sigma)["home_win_prob"] for x in m]
    out["model_sigma"] = sigma
    out["line_sigma"] = float((trl["margin"] - trl["spread_line"]).std())  # training seasons only
    parts.append(out)
    print("fold", f["val_season"], len(out), flush=True)
D = pd.concat(parts, ignore_index=True)

# starting QB = most dropbacks in the game (same rule as features/qb.py), previous starter per team
q = aux.qb_games.sort_values(["game_id", "team", "dropbacks", "qb_id"], ascending=[True, True, False, True])
starter = q.drop_duplicates(["game_id", "team"]).set_index(["game_id", "team"])["qb_id"]
D["home_qb"] = [starter.get((gid, t)) for gid, t in zip(D.game_id, D.home_team)]
D["away_qb"] = [starter.get((gid, t)) for gid, t in zip(D.game_id, D.away_team)]

sched = pd.concat([pd.read_parquet(p) for p in sorted(glob.glob("data/cache/nflverse/schedules_*.parquet"))])
sched = sched[["game_id", "home_coach", "away_coach", "home_moneyline", "away_moneyline", "game_type"]]
D = D.merge(sched, on="game_id", how="left")
D.to_csv(sys.argv[1], index=False)
print("rows", len(D), "seasons", D.season.min(), D.season.max(), "with spread", D.spread_line.notna().sum())
