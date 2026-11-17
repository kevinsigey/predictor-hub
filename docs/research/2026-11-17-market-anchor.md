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

(filled in after the preregistration commit)
