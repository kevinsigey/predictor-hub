# Market anchor study: model vs line, blends, and where the model adds anything (2026-11-17)

Research only. Nothing here is served, trained into an artifact, merged into a site repo, or deployed.
Scripts and raw logs: `docs/research/2026-11-17-market-anchor/`.

## 1. Preregistration (written and committed BEFORE any result below was computed)

### 1.1 Question

Every live comparison has the models losing to the Kalshi price (paper ledger Q1, pooled paired log-loss
gap about -0.04, CI excluding 0). Before deciding what the sites headline, measure on history:

1. model alone vs line alone vs a walk-forward blend, per sport where historical lines exist;
2. whether any preregistered segment shows the model carrying information the line lacks;
3. what to headline, whether a "model edge" flag survives anywhere, and which missing information
   would most plausibly close the gap.

### 1.2 Data available (inventory done before writing this section)

| Sport | Historical line on disk | Use |
|---|---|---|
| NFL | `data/cache/schedules/*.parquet` `spread_line`, `total_line` 2000-2026 (closing); `nflverse/schedules_*.parquet` moneylines, starting QB ids, head coaches 2017-2026 | full study |
| PL | football-data CSVs inside the PL training frame: Bet365 opening 1X2 (all seasons), Pinnacle closing 1X2 | full study, 1X2 only |
| CFB | none (`data/cache/odds/` empty; 92 sportsbook event JSONs and one Kalshi lines day are not a history) | live paper ledger only |
| NBA | none (`data/cache/odds/` empty; ESPN scoreboard cache carries no odds; 37 sportsbook events, 8 Kalshi lines days) | live paper ledger only |

Lines are closing (NFL) or opening/closing (PL). They are the comparator, never a model feature.

### 1.3 Models scored

- NFL: production game model as on `NFL_Predictor` origin/main (ridge, the 10 `FEATURE_COLUMNS`, no blocks), through the
  repo's own `evaluate/walk_forward.prepare_folds` (expanding seasons, `min_train_seasons=2`), margin and total, win
  probability from `margin_to_probabilities` with the fold's honest sigma. Seasons 2000-2026 loaded, validation 2002-2026.
- PL: production scoreline model on `PL_Predictor` origin/main (`ml_scoreline.train_goal_regressors` +
  `predict_grids_batch` on `build_training_frame` columns), expanding-season walk-forward (validation = every season
  after the first three, as in the 2026-10-13 study).
- Live: every Q1 point the hub's `paper.report` builds (same exclusions: in-play, unresolved start, same-p, legacy totals).

### 1.4 Line probabilities and blends (fixed before results)

- NFL line margin = `spread_line` (home expected margin), line total = `total_line`.
- NFL line win probability = Phi(spread_line / s), s = std of (margin - spread_line) over the TRAINING seasons of
  the fold. Check (2017+ only): de-vigged moneyline (proportional normalisation of 1/decimal odds).
- PL line probabilities = de-vigged (proportional) Pinnacle closing 1X2 (primary), Bet365 opening (secondary).
- Point blend: `b = line + w * (model - line)`. Probability blend (binary): `logit b = logit q + w * (logit p - logit q)`.
  3-way (PL): log-linear pool `b ~ q^(1-w) * p^w`, renormalised.
- w for validation season s is fitted ONLY on out-of-fold predictions from seasons < s (grid 0.00..1.00 step 0.01,
  minimising the season's primary metric). w = 0 until 3 earlier OOF seasons exist. Live ledger: w for a game fitted on
  games with kickoff strictly before that game's kickoff day, w = 0 until 100 earlier games.
- Segment-specific w (2.2c below) is fitted the same way using only earlier seasons' games IN that segment.

### 1.5 Metrics and inference (fixed)

- Primary: NFL margin MAE (blend vs line); NFL win-prob log loss (blend vs line probability); PL 1X2 log loss
  (blend vs Pinnacle close). Secondary: NFL total MAE, Brier, PL RPS, Bet365-open comparisons.
- Every comparison is paired per game; 95% CI by bootstrap over games (2,000 resamples, seed 0). Live ledger: the
  hub's cluster bootstrap (`stats.cluster_ci`, games as clusters).
- Side test (NFL): for |model - line| >= threshold, hit = sign(model - line) == sign(actual - line), pushes dropped.
  Breakeven at -110 is 52.38%. Wilson 95% interval.

### 1.6 Preregistered segments (NO others will be reported as findings)

NFL (validation seasons 2002-2026 unless stated):

| id | segment | definition |
|---|---|---|
| N1 | week band | weeks 1-4 / 5-9 / 10-end of regular season / postseason (week > 17 before 2021, > 18 from 2021) |
| N2 | early vs late | regular-season weeks 1-8 vs 9+ |
| N3 | QB change | either team's starting QB (most dropbacks, from `pbp_agg.qb_games`) differs from that team's previous game's starter; first game of a season compares to the previous season's last game |
| N4 | new head coach | either team's head coach differs from that team's coach in its last game of the previous season, first 8 games of the season only; 2018-2026 (coach data from 2017) |
| N5 | margin disagreement | |model margin - spread_line| < 1.5 / 1.5-3 / 3-6 / >= 6 |
| N6 | total disagreement | |model total - total_line| same buckets (total MAE and over/under side test) |
| N7 | era | 2002-2012 / 2013-2026 |

PL (validation seasons):

| id | segment | definition |
|---|---|---|
| P1 | early season | either team has played <= 6 league matches that season / both > 6 |
| P2 | promoted team | either team was not in the previous season's frame |
| P3 | disagreement | |model P(home) - Pinnacle P(home)| < 0.05 / 0.05-0.10 / >= 0.10 |

Live ledger (Q1 points):

| id | segment | definition |
|---|---|---|
| L1 | sport x market | e.g. cfb:spread |
| L2 | disagreement | |model_p - market_price| < 0.05 / 0.05-0.10 / >= 0.10 |
| L3 | price lead time | price observed >= 24 h before kickoff / < 24 h |
| L4 | CFB power vs other | both teams in ACC/Big Ten/Big 12/SEC (+Notre Dame) vs at least one other (CFB rows only; conference from `data/cache/games/2026.parquet`) |

### 1.7 Decision rules (fixed)

- **Headline.** If the walk-forward blend's primary-metric improvement over the line has a CI excluding 0 in the
  model's favour, headline the blend; otherwise headline the line (market) probability and show the model alongside
  as "model view", never as an edge.
- **Model-edge flag.** A segment justifies a flag only if BOTH (a) its segment-specific blend beats the line with a
  Bonferroni-adjusted CI (alpha = 0.05 / number of segment cells tested in that sport) excluding 0, and (b) where a side
  test applies, the Bonferroni-adjusted Wilson lower bound of the side hit rate exceeds 52.38%. Unadjusted 95% CIs are
  also printed but cannot justify a flag.
- **Information sources ranking.** Expected value of a source = (share of games in the segment it would inform) x
  (excess model-minus-line error in that segment, per game). Ranked by that product, then by data cost (on disk / free
  API / paid).
- Nothing is tuned on a validation season; w for season s never sees season s.

## 2. Results

Section 1 was committed (90a3250, 12:06:26) before the first result file was written (12:08). Two details were fixed
while writing the scripts, before any segment output existed, and are stated here rather than hidden:
the per-cell NFL side test uses games with |model - line| >= 1.5 (the N5 buckets give the other thresholds), and the
live side test uses |model_p - price| >= 0.05. P1 "<= 6 played" counts the match itself.

### 2.0 Answer in one screen

- **The line wins everywhere, and the walk-forward blend puts zero weight on the model.** Fitted only on earlier
  seasons, w = 0.00 for NFL margin in all 25 seasons, w <= 0.04 for NFL win probability, w = 0.00 for PL 1X2 in
  every season against both Pinnacle close and Bet365 open, and w = 0.00 on all 21 live kickoff days where it could
  be fitted. Only NFL totals get w = 0.07-0.18, and that blend ties the line (-0.0017 pts [-0.0105, +0.0069]).
- **No preregistered segment shows model information the line lacks.** 0 of 20 NFL cells, 0 of 7 PL cells, 0 of 15
  live cells meet the flag rule; none even has an unadjusted 95% CI above zero for model or segment blend.
- **Recommendation: headline the market probability. Show the model next to it, labelled "model view", never as an edge.
  Keep the "model edge" flag off.** The first data to add is who actually starts at QB (NFL), then injury/availability news (NBA first).

### 2.1 Overall: model vs line vs walk-forward blend

Positive = candidate better than the line; paired bootstrap over games, 2,000 resamples.

| Sport, metric | N | Model | Line | Blend | Line - model [95% CI] | Line - blend [95% CI] | w by season |
|---|---|---|---|---|---|---|---|
| NFL margin MAE (pts), 2002-2026 | 6,649 | 10.584 | 10.234 | 10.234 | **-0.350 [-0.427, -0.275]** | +0.000 [+0.000, +0.000] | 0.00 every season |
| NFL total MAE (pts) | 6,649 | 10.882 | 10.557 | 10.559 | **-0.324 [-0.407, -0.246]** | -0.002 [-0.011, +0.007] | 0.00 to 2007, then 0.07-0.18 |
| NFL win log loss (line = Phi(spread/s)) | 6,633 | 0.6320 | 0.6117 | 0.6117 | **-0.0203 [-0.0248, -0.0157]** | -0.0000 [-0.0001, +0.0000] | 0.00-0.04 |
| NFL win Brier | 6,633 | 0.2211 | 0.2118 | 0.2118 | -0.0093 [-0.0114, -0.0072] | -0.0000 | |
| NFL win log loss vs de-vig moneyline, 2017-2026 | 2,577 | 0.6348 | 0.6092 | 0.6104 | **-0.0256 [-0.0338, -0.0174]** | -0.0012 [-0.0034, +0.0012] | |
| PL 1X2 log loss vs Pinnacle close, 2021-22..2025-26 | 1,730 | 0.9708 | 0.9468 | 0.9468 | **-0.0240 [-0.0326, -0.0150]** | +0.0000 | 0.00 every season |
| PL 1X2 RPS vs Pinnacle close | 1,730 | 0.1990 | 0.1918 | 0.1918 | -0.0072 [-0.0099, -0.0045] | -0.0000 | |
| PL 1X2 log loss vs Bet365 open | 1,900 | 0.9785 | 0.9604 | 0.9604 | **-0.0181 [-0.0253, -0.0107]** | +0.0000 | 0.00 every season |
| Live Kalshi Q1, pooled NFL+NBA+CFB (hub statistic) | 439 games | | | | **-0.0372 [-0.0542, -0.0208]** | -0.0000 | 0.00 on all 21 fitted days (332 games) |

The live population is exactly the hub report's Q1 (442 games in `data/paper/report.json` = these 439 + 3 F1 races).
Recent NFL windows say the same: 2019-2026 margin line - blend +0.000, total -0.010 [-0.026, +0.005];
2023-2026 total -0.010 [-0.030, +0.011].

Side tests (does the direction of disagreement pick the winner against the line?):
NFL |model - spread| >= 4: 704/1,356 = 51.9% (Wilson 95% 49.3-54.6%), below the 52.38% breakeven.
NFL |model - total| >= 4: 857/1,682 = 50.9% (48.6-53.3%). Live, |model_p - price| >= 0.10: 42.3% (39.1-45.4%):
**when the live model disagrees by 10+ points it is on the wrong side more often than not.**

### 2.2 Segments (preregistered; full tables in `nfl_segments.csv`, `pl_segments.csv`, `live_segments.csv`)

NFL (20 cells, Bonferroni alpha 0.0025). "Model gain" = line MAE - model MAE. "Seg-blend gain" uses a w fitted only on
earlier seasons inside the cell. Side = hit rate when |model - line| >= 1.5 (Bonferroni-adjusted Wilson lower bound).

| Cell | N | Model gain (pts) [95%] | Seg-blend gain [95%] | Win LL model gain | Side hit (adj. lower) |
|---|---|---|---|---|---|
| N1 weeks 1-4 | 1,559 | -0.410 [-0.583, -0.240] | -0.001 [-0.003, +0.001] | -0.0165 | 50.3% (45.6) |
| N1 weeks 5-9 | 1,753 | -0.235 [-0.376, -0.101] | -0.014 [-0.042, +0.013] | -0.0144 | 52.0% (47.4) |
| N1 weeks 10+ | 3,061 | -0.396 [-0.504, -0.286] | 0.000 | -0.0260 | 49.3% (45.7) |
| N1 postseason | 276 | -0.233 [-0.551, +0.104] | -0.073 [-0.184, +0.034] | -0.0160 | 51.5% (40.0) |
| N2 weeks 1-8 | 2,969 | -0.321 [-0.436, -0.208] | -0.002 [-0.007, +0.003] | -0.0155 | 50.9% (47.4) |
| N2 weeks 9+ | 3,404 | -0.385 [-0.490, -0.278] | -0.002 [-0.003, -0.000] | -0.0248 | 49.7% (46.3) |
| N3 QB change: yes | 1,423 | **-0.581 [-0.788, -0.392]** | 0.000 | **-0.0377** | 50.9% (45.9) |
| N3 QB change: no | 5,226 | -0.287 [-0.370, -0.213] | -0.001 [-0.003, +0.001] | -0.0156 | 50.2% (47.5) |
| N4 new coach (2018+): yes | 467 | -0.467 [-0.756, -0.177] | +0.001 [-0.031, +0.033] | -0.0240 | 49.7% (41.1) |
| N4 new coach (2018+): no | 1,910 | -0.407 [-0.547, -0.267] | -0.002 [-0.012, +0.009] | -0.0245 | 50.4% (46.0) |
| N5 |model-spread| < 1.5 | 2,504 | -0.030 [-0.062, +0.003] | -0.007 [-0.023, +0.011] | -0.0013 | n/a |
| N5 1.5-3 | 1,907 | -0.227 [-0.323, -0.131] | 0.000 | -0.0137 | 49.1% (45.6) |
| N5 3-6 | 1,772 | -0.512 [-0.694, -0.329] | 0.000 | -0.0281 | 51.0% (47.4) |
| N5 >= 6 | 466 | **-1.958 [-2.607, -1.262]** | +0.001 [-0.020, +0.023] | **-0.1191** | 52.8% (45.8) |
| N6 |model-total| < 1.5 | 2,199 | +0.000 [-0.036, +0.037] | -0.009 [-0.024, +0.006] | | n/a |
| N6 1.5-3 | 1,797 | -0.152 [-0.254, -0.058] | -0.002 [-0.005, +0.002] | | 50.1% (46.5) |
| N6 3-6 | 2,031 | -0.521 [-0.689, -0.342] | -0.002 [-0.016, +0.012] | | 50.9% (47.5) |
| N6 >= 6 | 622 | -1.327 [-1.872, -0.783] | -0.016 [-0.096, +0.068] | | 52.2% (46.1) |
| N7 2002-2012 | 2,937 | -0.340 [-0.451, -0.233] | 0.000 | -0.0214 | 49.3% (45.7) |
| N7 2013-2026 | 3,712 | -0.359 [-0.462, -0.255] | -0.011 [-0.030, +0.007] | -0.0193 | 51.1% (48.0) |

The pattern is monotone: **the further the model is from the line, the worse it is**, by up to 2 points of MAE per game
at >= 6 points of disagreement. That is the signature of a model missing information the line has, not of a model
finding something the line missed.

PL vs Pinnacle close (7 cells, Bonferroni alpha 0.0071), log-loss gain of the model (seg-blend w = 0.00-0.21, every seg-blend gain within +-0.0002):
early (<= 6th match) -0.022 [-0.045, +0.002], later -0.024 [-0.034, -0.015]; promoted team involved -0.032 [-0.047, -0.016],
not -0.021 [-0.031, -0.010]; |dP(home)| < 0.05 -0.003 [-0.009, +0.004], 0.05-0.10 -0.019 [-0.034, -0.005], >= 0.10 **-0.089 [-0.121, -0.055]**.
Same monotone pattern as NFL.

Live Kalshi (15 cells, Bonferroni alpha 0.0033), per-game log-loss gain of the model:

| Cell | Games | Model gain [95%] | Side hit at >= 0.05 (95% Wilson) |
|---|---|---|---|
| cfb:moneyline | 130 | -0.024 [-0.070, +0.021] | 52.0% (42.3-61.5) |
| cfb:spread | 149 | -0.039 [-0.081, +0.006] | 44.3% (40.3-48.4) |
| cfb:total | 145 | -0.018 [-0.057, +0.021] | 50.5% (46.2-54.7) |
| nba:moneyline | 202 | **-0.049 [-0.076, -0.022]** | **32.5% (24.7-41.4)** |
| nba:spread | 202 | -0.018 [-0.048, +0.010] | 53.3% (44.5-61.9) |
| nba:total | 201 | -0.002 [-0.040, +0.040] | 54.7% (46.4-62.7) |
| nfl:moneyline | 37 | -0.053 [-0.100, -0.006] | 41.7% (24.5-61.2) |
| nfl:total | 32 | -0.098 [-0.209, +0.002] | 40.6% (31.7-50.1) |
| |model - price| < 0.05 | 198 | -0.003 [-0.010, +0.004] | n/a |
| 0.05-0.10 | 295 | -0.022 [-0.038, -0.006] | 53.1% (49.5-56.6) |
| >= 0.10 | 294 | **-0.058 [-0.094, -0.021]** | 42.3% (39.1-45.4) |
| price >= 24 h before start | 290 | -0.039 [-0.063, -0.016] | 45.8% |
| price < 24 h before start | 291 | -0.039 [-0.061, -0.019] | 49.7% |
| CFB power v power | 97 | -0.014 [-0.052, +0.025] | 46.0% |
| CFB other (G5 / independents) | 95 | **-0.066 [-0.103, -0.029]** | 49.3% |

Small-conference CFB is where the model is worst, not where it has an edge. A stale early price does not help the
model either (identical gap at >= 24 h and < 24 h).

### 2.3 Where the gap comes from (NFL decomposition, `nfl_analyze.log`)

Pooled model-minus-line margin MAE is +0.350. Contribution = share of games x (excess in segment - excess elsewhere):

| Source | Share of games | Excess in / out (pts) | Attributable (pts of 0.350) |
|---|---|---|---|
| Starting-QB change (either team) | 21.4% | +0.581 / +0.287 | +0.063 (and 0.124 of the gap sits in these games) |
| |model - spread| >= 6 | 7.0% | +1.958 / +0.229 | +0.121 |
| ... of which also a QB change | 2.8% | +2.615 / +0.284 | +0.066 |
| Weeks 1-4 | 23.4% | +0.410 / +0.332 | +0.019 |
| New head coach (2018+, weeks 1-8) | 7.0% | +0.467 / +0.341 | +0.009 |

40.3% of the model's >= 6-point disagreements are QB-change games (21.4% base rate): about half of the model's
biggest misses are the market knowing who starts at QB.

## 3. Recommendations

**(a) What to headline.** The de-vigged market probability (Kalshi price where it exists; the closing line in
backtests). The preregistered rule picks "blend" only if the blend beats the line with a CI excluding zero. It never
does, and the fitted blend IS the line (w = 0). Show the model side by side as "model view", with the gap in points,
and never as a recommendation. NFL totals are the only market with w > 0 (about 0.10), and that blend ties the line,
so there is no reason to special-case it.

**(b) "Model edge" flag.** Off. 0 of 42 preregistered cells qualify. The best unadjusted side rate is 52.8% at
NFL |model - spread| >= 6 (n = 458, adjusted lower bound 45.8%), still below the 52.38% breakeven. Live, the model's
big disagreements lose more often than they win (42.3% at >= 0.10, NBA moneyline 32.5%). A flag would point readers at
the model's worst predictions.

**(c) Missing information, ranked by expected value (measured share x excess where a segment exists), then data cost:**

| Rank | Source | Evidence | Data cost |
|---|---|---|---|
| 1 | **NFL confirmed starting QB + that QB's quality** | 21% of games; 0.124 of the 0.350 gap sits in them; 40% of >= 6-pt misses. The on-disk `qb` block (season-to-date QB EPA) moved MAE only +0.073 [+0.020, +0.126] in the 2026-10-13 block eval (READ, `NFL_Predictor/docs/nfl_block_eval_results.md`), so what is missing is *who starts this week*, not QB history | low: nflverse `home/away_qb_id` (2017+) and pbp starters are on disk; serving needs the weekly starter (depth chart / injury report), which is free |
| 2 | **NBA player availability (stars resting / injured)** | no preregistered segment, so not measured here; NBA moneyline is the worst live cell (-0.049, side 32.5%), which fits a model that does not know who plays | low-medium: `NBA_Predictor/data/cache/injuries` exists; needs a history to backtest |
| 3 | **NFL non-QB injuries / inactives** | not measured (no preregistered segment); nflverse `injuries_YYYY` with `practice_status` 2017-2026 is on disk and unused | low |
| 4 | Early-season roster turnover (NFL weeks 1-4, PL early season, promoted clubs) | NFL +0.019 pts attributable; PL early -0.022 vs later -0.024 and promoted -0.032 vs -0.021, CIs overlap | medium (transfer / returning-production data not on disk; CFB returning production would need CFBD) |
| 5 | Head-coach change | +0.009 pts attributable | on disk (2017+), not worth a feature |

The ship rule for any of these is the repo's own: walk-forward, paired CI excluding zero against the model AND the
gap to the line narrowing. Given w = 0 everywhere, the honest bar for "the sites should show the model as more than
context" is the blend weight leaving zero with a CI.

## 4. What is not verified

- QB change uses the starter who actually played (most dropbacks), so N3 is an upper bound on what a pre-kickoff
  starter feed could know (late scratches are rare but counted).
- NFL lines are closing lines; an opening-line comparison would be kinder to the model and was not possible (no
  opening lines on disk). Kalshi prices in the ledger are pre-start snapshots, not closes.
- CFB and NBA have no historical lines on disk, so for them only the live ledger (192 and 202 games) speaks; F1 is
  excluded (3 races).
- The live start times come from the sites' origin/main `public_snapshot.json` on 2026-11-17; with them the
  population matches the committed report exactly (439 game-sport games + 3 F1).
- NFL harness is the production ridge with expanding training windows; production retrains on a rolling
  2018-2025 window, so a served model's numbers can differ a little from these.

## 5. Reproduce

`A=docs/research/2026-11-17-market-anchor`. Python envs: each repo's `.venv`.

```
# NFL (cwd = an NFL_Predictor checkout with data/cache; PYTHONPATH = NFL origin/main src, extracted with git archive)
python $A/nfl_oof.py $A/nfl_oof.csv.gz > $A/nfl_oof.log
cd $A && python nfl_analyze.py nfl_oof.csv.gz nfl_segments.csv > nfl_analyze.log
# PL (frame = build_training_frame(load_training_data(default_completed_seasons())) from PL origin/main, pickled)
cd $A && PYTHONPATH=<PL src>:. python pl_analyze.py <pl_frame.pkl> <pl_cols.json> > pl_analyze.log
# Live (sites root holding <Site>/data/public_snapshot.json from each site's origin/main)
cd $A && python live_analyze.py <sites_root> <CFB data/cache/games/2026.parquet> > live_analyze.log
python $A/common.py   # self-check for the blend / bootstrap helpers
```

Raw per-game outputs are stored beside the summaries: `nfl_oof.csv.gz`, `pl_oof.csv.gz`, `live_points.csv.gz`.
