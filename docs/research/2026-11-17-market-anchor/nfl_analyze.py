"""NFL market-anchor analysis on nfl_oof.csv.gz (preregistered in ../2026-11-17-market-anchor.md section 1).

    python nfl_analyze.py nfl_oof.csv.gz > nfl_analyze.log
Positive "gain" everywhere = the candidate (model or blend) is better than the line.
"""
import math
import sys

import numpy as np
import pandas as pd

from common import blend_p, boot, ll, walk_forward_w, wilson, z_for

D = pd.read_csv(sys.argv[1]).sort_values(["season", "week", "game_id"]).reset_index(drop=True)
D = D[D.spread_line.notna() & D.total_line.notna()].reset_index(drop=True)
Phi = np.vectorize(lambda x: 0.5 * math.erfc(-x / math.sqrt(2)))
D["line_p"] = Phi(D.spread_line / D.line_sigma)
D["y"] = (D.margin > 0).astype(float)
tie = D.margin == 0


def ml_prob(ml):
    ml = np.asarray(ml, float)
    return np.where(ml < 0, -ml / (-ml + 100), 100 / (ml + 100))


h, a = ml_prob(D.home_moneyline), ml_prob(D.away_moneyline)
D["ml_p"] = h / (h + a)
S = D.season.to_numpy()

# ---- walk-forward global w per season (fitted on earlier seasons' OOF rows only)
err = lambda pred, act: np.abs(pred - act)
wm = walk_forward_w(S, lambda m, w: err(D.spread_line[m] + w * (D.model_margin[m] - D.spread_line[m]), D.margin[m]).mean())
wt = walk_forward_w(S, lambda m, w: err(D.total_line[m] + w * (D.model_total[m] - D.total_line[m]), D.total_points[m]).mean())
pm = ~tie.to_numpy()
wp = walk_forward_w(S, lambda m, w: ll(blend_p(D.line_p[m & pm], D.model_p[m & pm], w), D.y[m & pm]).mean())
D["w_m"], D["w_t"], D["w_p"] = [D.season.map(x) for x in (wm, wt, wp)]
D["blend_margin"] = D.spread_line + D.w_m * (D.model_margin - D.spread_line)
D["blend_total"] = D.total_line + D.w_t * (D.model_total - D.total_line)
D["blend_p"] = blend_p(D.line_p, D.model_p, D.w_p)
print("walk-forward w by season (margin / total / win-prob):")
for s in sorted(wm):
    print(f"  {s}: {wm[s]:.2f} / {wt[s]:.2f} / {wp[s]:.2f}")

E = {k: err(D[f"{k}_margin"] if k != "line" else D.spread_line, D.margin) for k in ("model", "blend", "line")}
T = {k: err(D[f"{k}_total"] if k != "line" else D.total_line, D.total_points) for k in ("model", "blend", "line")}
L = {k: ll(D[c], D.y) for k, c in (("model", "model_p"), ("blend", "blend_p"), ("line", "line_p"), ("ml", "ml_p"))}
B = {k: (D[c] - D.y) ** 2 for k, c in (("model", "model_p"), ("blend", "blend_p"), ("line", "line_p"), ("ml", "ml_p"))}


def fmt(t):
    return f"{t[0]:+.4f} [{t[1]:+.4f}, {t[2]:+.4f}]"


print(f"\n== OVERALL: N={len(D)} games, seasons {S.min()}-{S.max()} (prob metrics drop {int(tie.sum())} ties)")
for name, X in (("margin MAE", E), ("total MAE", T)):
    print(f"{name}: model {X['model'].mean():.4f}  blend {X['blend'].mean():.4f}  line {X['line'].mean():.4f}")
    print(f"   line-model {fmt(boot(X['line'] - X['model']))}   line-blend {fmt(boot(X['line'] - X['blend']))}   model-blend {fmt(boot(X['model'] - X['blend']))}")
for name, X in (("win log loss", L), ("win Brier", B)):
    v = {k: X[k][pm] for k in X}
    print(f"{name}: model {v['model'].mean():.4f}  blend {v['blend'].mean():.4f}  line(spread->Phi) {v['line'].mean():.4f}")
    print(f"   line-model {fmt(boot(v['line'] - v['model']))}   line-blend {fmt(boot(v['line'] - v['blend']))}")
mlm = pm & D.ml_p.notna().to_numpy()
print(f"moneyline check (2017+, N={mlm.sum()}): log loss model {L['model'][mlm].mean():.4f} spread-Phi {L['line'][mlm].mean():.4f} "
      f"de-vig ML {L['ml'][mlm].mean():.4f} blend {L['blend'][mlm].mean():.4f}; ML-model {fmt(boot(L['ml'][mlm] - L['model'][mlm]))} "
      f"ML-blend {fmt(boot(L['ml'][mlm] - L['blend'][mlm]))}")
for lab, m in (("2019-2026", S >= 2019), ("2023-2026", S >= 2023)):
    print(f"  [{lab} N={m.sum()}] margin line-blend {fmt(boot(E['line'][m] - E['blend'][m]))}; "
          f"total line-blend {fmt(boot(T['line'][m] - T['blend'][m]))}; logloss line-blend {fmt(boot((L['line'] - L['blend'])[m & pm]))}")

# ---- preregistered segments
post = np.where(D.season < 2021, D.week > 17, D.week > 18)
reg_week = D.week.where(~post)
dm = (D.model_margin - D.spread_line).abs()
dt = (D.model_total - D.total_line).abs()


def bucket(x):
    return pd.cut(x, [-0.01, 1.5, 3, 6, 1e9], right=False, labels=["<1.5", "1.5-3", "3-6", ">=6"]).astype(str)


long = pd.concat([D[["game_id", "season", "week", "home_team", "home_qb", "home_coach"]].set_axis(["game_id", "season", "week", "team", "qb", "coach"], axis=1),
                  D[["game_id", "season", "week", "away_team", "away_qb", "away_coach"]].set_axis(["game_id", "season", "week", "team", "qb", "coach"], axis=1)])
long = long.sort_values(["team", "season", "week"])
long["prev_qb"] = long.groupby("team").qb.shift(1)
long["qb_change"] = long.qb.notna() & long.prev_qb.notna() & (long.qb != long.prev_qb)
last_coach = long.dropna(subset=["coach"]).groupby(["team", "season"]).coach.last()
long["prev_season_coach"] = [last_coach.get((t, s - 1)) for t, s in zip(long.team, long.season)]
long["game_no"] = long.groupby(["team", "season"]).cumcount() + 1
long["new_coach"] = long.prev_season_coach.notna() & long.coach.notna() & (long.coach != long.prev_season_coach) & (long.game_no <= 8)
anyof = long.groupby("game_id")[["qb_change", "new_coach"]].any()
D["qb_change"] = D.game_id.map(anyof.qb_change).fillna(False).astype(bool)
D["new_coach"] = D.game_id.map(anyof.new_coach).fillna(False).astype(bool)
coach_era = S >= 2018

cells = []
cells += [("N1 week", lab, m) for lab, m in (("1-4", reg_week <= 4), ("5-9", reg_week.between(5, 9)), ("10+", reg_week >= 10), ("post", post))]
cells += [("N2 half", lab, m) for lab, m in (("wk1-8", reg_week <= 8), ("wk9+", reg_week >= 9))]
cells += [("N3 QB change", lab, m) for lab, m in (("yes", D.qb_change), ("no", ~D.qb_change))]
cells += [("N4 new coach 2018+", lab, m) for lab, m in (("yes", D.new_coach & coach_era), ("no", ~D.new_coach & coach_era))]
cells += [("N5 |model-spread|", lab, bucket(dm) == lab) for lab in ["<1.5", "1.5-3", "3-6", ">=6"]]
cells += [("N6 |model-total|", lab, bucket(dt) == lab) for lab in ["<1.5", "1.5-3", "3-6", ">=6"]]
cells += [("N7 era", lab, m) for lab, m in (("2002-12", S <= 2012), ("2013-26", S >= 2013))]
K = len(cells)
a_adj = 0.05 / K
zb = z_for(a_adj)
print(f"\n== SEGMENTS ({K} cells; Bonferroni alpha {a_adj:.5f}, z {zb:.3f}). gain = line error - candidate error, positive = candidate better")
rows = []
for seg, lab, m in cells:
    m = np.asarray(m, bool)
    total = seg.startswith("N6")
    act, line, mod = (D.total_points, D.total_line, D.model_total) if total else (D.margin, D.spread_line, D.model_margin)
    act, line, mod = act[m].to_numpy(), line[m].to_numpy(), mod[m].to_numpy()
    glob_b = (D.blend_total if total else D.blend_margin)[m].to_numpy()
    ws = walk_forward_w(S[m], lambda mm, w: np.abs(line[mm] + w * (mod[mm] - line[mm]) - act[mm]).mean())
    wseg = np.array([ws[s] for s in S[m]])
    seg_b = line + wseg * (mod - line)
    le = np.abs(line - act)
    g_model, g_glob, g_seg = boot(le - np.abs(mod - act)), boot(le - np.abs(glob_b - act)), boot(le - np.abs(seg_b - act))
    g_seg_adj = boot(le - np.abs(seg_b - act), alpha=a_adj)
    mp = m & pm
    wsp = walk_forward_w(S[mp], lambda mm, w: ll(blend_p(D.line_p[mp][mm], D.model_p[mp][mm], w), D.y[mp][mm]).mean())
    segp = blend_p(D.line_p[mp], D.model_p[mp], np.array([wsp[s] for s in S[mp]]))
    lp = (L["line"][mp] - ll(segp, D.y[mp])).to_numpy()
    p_seg, p_seg_adj = boot(lp), boot(lp, alpha=a_adj)
    p_model = boot((L["line"] - L["model"])[mp])
    side = (np.abs(mod - line) >= 1.5) & (act != line)
    hit = np.sign(mod - line)[side] == np.sign(act - line)[side]
    k, n = int(hit.sum()), int(side.sum())
    wl = wilson(k, n, zb)
    rows.append(dict(segment=seg, cell=lab, n=int(m.sum()), share=m.mean(), model_mae=np.abs(mod - act).mean(), line_mae=le.mean(),
                     gain_model=g_model[0], gain_model_lo=g_model[1], gain_model_hi=g_model[2],
                     gain_globalblend=g_glob[0], gg_lo=g_glob[1], gg_hi=g_glob[2],
                     gain_segblend=g_seg[0], gs_lo=g_seg[1], gs_hi=g_seg[2], gs_adj_lo=g_seg_adj[1], gs_adj_hi=g_seg_adj[2],
                     last_seg_w=float(wseg[-1]) if len(wseg) else np.nan,
                     ll_gain_model=p_model[0], llm_lo=p_model[1], llm_hi=p_model[2],
                     ll_gain_segblend=p_seg[0], llg_lo=p_seg[1], llg_hi=p_seg[2], llg_adj_lo=p_seg_adj[1], llg_adj_hi=p_seg_adj[2],
                     side_n=n, side_hit=k / n if n else np.nan, side_lo_adj=wl[0], side_hi_adj=wl[1],
                     flag=bool(g_seg_adj[1] > 0 and (n == 0 or wl[0] > 0.5238))))
R = pd.DataFrame(rows)
pd.set_option("display.width", 320)
pd.set_option("display.max_columns", 40)
print(R.round(4).to_string(index=False))
R.to_csv(sys.argv[2] if len(sys.argv) > 2 else "nfl_segments.csv", index=False)

# side test at the user's threshold for reference (preregistered N5 covers it; this is the >=4 row)
side = (dm >= 4) & (D.margin != D.spread_line)
hit = (np.sign(D.model_margin - D.spread_line) == np.sign(D.margin - D.spread_line))[side]
print(f"\nside test |model-spread|>=4: {int(hit.sum())}/{int(side.sum())} = {hit.mean():.4f}, Wilson95 {wilson(int(hit.sum()), int(side.sum()))}")
side = (dt >= 4) & (D.total_points != D.total_line)
hit = (np.sign(D.model_total - D.total_line) == np.sign(D.total_points - D.total_line))[side]
print(f"side test |model-total|>=4: {int(hit.sum())}/{int(side.sum())} = {hit.mean():.4f}, Wilson95 {wilson(int(hit.sum()), int(side.sum()))}")
print("\nflags:", R[R.flag][["segment", "cell"]].values.tolist() or "none")

# ---- gap decomposition (feeds the information-source ranking, section 1.7): contribution = share x per-game excess
gap = E["model"] - E["line"]
print(f"\n== GAP DECOMPOSITION: pooled model-minus-line margin MAE {gap.mean():+.4f}")
for name, m in (("QB change", D.qb_change), ("new coach (2018+, wk<=8)", D.new_coach & coach_era), ("weeks 1-4", reg_week <= 4),
                ("|model-spread|>=6", dm >= 6), ("|model-spread|>=6 AND QB change", (dm >= 6) & D.qb_change)):
    m = np.asarray(m, bool)
    ex_in, ex_out = gap[m].mean(), gap[~m].mean()
    print(f"  {name}: share {m.mean():.3f}, excess in {ex_in:+.3f} vs out {ex_out:+.3f}; "
          f"share x (in - out) = {m.mean() * (ex_in - ex_out):+.4f} pts; share x in = {gap[m].sum() / len(gap):+.4f} pts of the gap")
print(f"  QB change rate inside |model-spread|>=6: {D.qb_change[dm >= 6].mean():.3f} vs overall {D.qb_change.mean():.3f}")
