# Data coverage and new angles (2026-11-18)

Research only. Nothing served, trained, or deployed changes. Probes are **exploratory** (written after looking at the
data, several cells, no multiplicity correction). They suggest tests; they are not results that justify shipping.
Script: `scripts/research/data_angles_probes.py` (stdlib; `--selftest`). Raw output: `scripts/research/data_angles_probes.log`.

Kevin's question: are we using all the data we could, and are there new ways to look at it?
Short answer: **no. The served game models use only their base columns. EPA, QB, weather, injury, practice-session and
all market-path data sit on disk unused. But adding more pregame data to the game models is the one
thing that has already failed three times today** (NBA injury report #177, NFL starting QB #110, PL Dixon-Coles rho
#125, on top of the market-anchor study where the blend gives the model w = 0). The open angles are about *the market*,
not more team-strength inputs: when the price moves, props the models already price, F1 practice pace, and which
Kalshi side is overpriced.

## Ranked shortlist

| # | Angle | Probe on disk (exploratory, game-clustered 95% CI) | Data | Cost |
|---|---|---|---|---|
| 1 | **Line movement / timing.** The model's disagreement with an early price predicts which way the price moves before the start. | Live Kalshi: following the model's side gains **+0.73 pts [+0.42, +1.05]** of price by the last pre-start snapshot (2,379 markets, 560 games). Not mean reversion: the fade-the-favourite control is -0.20 [-0.45, +0.04], and where the model's side is *against* fade-the-favourite the gain is +1.05 [+0.63, +1.47]. PL history, model vs Pinnacle open: +0.19 pts [+0.08, +0.30] (1,730 matches), +0.47 [+0.18, +0.77] at disagreement of 0.10 or more | have (ledger + Kalshi snapshots; PL CSVs) | S |
| 2 | **Player props vs the props models we already serve.** NFL and NBA props models are served, and Kalshi prop markets are collected twice a day, but the two are never compared | Kalshi prop mids are calibrated (NBA outcome - mid +0.49 pts [-0.42, +1.48], 38,998 contracts / 180 games; NFL -0.87 [-2.58, +1.00], 18,142 / 71). So no free lunch, and no test of our models yet | have both sides; need a join | S-M |
| 3 | **F1 practice pace for qualifying markets** (pole, top-10). `POST_PRACTICE` feature tier is empty, while FastF1 has 11 GB on disk (2022-2026) | F1 Kalshi YES longshots (< 10c) settle 3.1 pts below mid [-4.5, -1.2], but only 13 events | have | M |
| 4 | **NO side of multi-outcome Kalshi markets** (PL 1X2, F1) | PL Kalshi 1X2: outcome - mid **-7.3 pts [-12.6, -1.8]**; buying NO at the ask nets +5.5c [-0.1, +10.8] after fees. Only 35 matches in one month | have, accumulating | S (wait for n) |
| 5 | **CFB line history from CFBD** (free key; opening and closing spreads, many books, 2013+). Fixes "CFB has no historical line" in the anchor study, and allows probe 1 on history | CFB moneyline has the largest live drift toward the model, +2.99 pts [+1.90, +4.09] (275 markets), the only cell above the roughly 2.75c round-trip cost | need: one API pull | S |
| 6 | Totals markets (NFL total is the only market with w > 0; PL O/U 2.5 open and close are on disk and never compared with the model) | not probed | have | S |
| 7 | Kalshi vs sportsbook consensus | Proxy on PL history finds **nothing**. When Pinnacle's close differs from the market-average close, results do not follow Pinnacle: +0.26 pts [-1.01, +1.48], log-loss gain +0.0001 [-0.0007, +0.0008], 2,490 matches. Betfair exchange vs average: -1.12 [-3.33, +1.09], 818 matches | need: schedule the sportsbook collector | S, low EV |

**Do not prioritise:** more pregame team-strength features for game-level markets (QB, injuries, travel, rho,
EPA blocks). Three negative results today plus w = 0 say the closing line already holds that information.

## Part 1. Data inventory per sport

"Served" means the columns in the committed `models/manifest.json` / `FEATURE_COLUMNS` on origin/main (read
2026-11-17), not docs. "Pre" means knowable before the start.

| Sport | On disk (cache, repo, collectors) | Reaches the SERVED model | Collected but unused | Free, not on disk (Pre?) |
|---|---|---|---|---|
| **NFL** | nflverse schedules 2000-2026 (closing spread/total, moneylines, QB ids, coaches); nflverse pbp + rosters + injuries + NGS 2017-2026 (10 seasons), weekly player stats; `pbp_agg` 2000-2026; depth charts 2001-2026; injuries 2009-2026; weather JSON for 1,262 games 2017+; Kalshi game + **props** snapshots twice a day since 10-17 (`data/lines`, ~33 days) | Game ridge: the 10 base columns only (rating x3, rolling points for/against x4, rest x2, `div_game`). `BLOCK_COLUMNS` epa / qb / conditions exist, `DEFAULT_BLOCKS = ()`. Props: 14 rolling player columns; availability features (injury, depth chart) feed props training only | EPA block, QB block (#110 negative), conditions/weather, injuries and depth charts for the game model, NGS, moneylines (comparator only), **Kalshi props never compared with the props models** (the paper ledger has no prop rows) | nflverse participation / snap counts (post-game only, so usable as lagged form); opening lines (SBR archives, about 2007-2022, Pre); FTN charting (post-game) |
| **CFB** | games 2000-2026 (scores, conferences, neutral); CFBD advanced stats 2014-2026 (repo + cache); weekly team stats 2004+; player stats 2018-2025; rosters 2025-26; one sportsbook snapshot (92 events, 10-14); Kalshi game lines twice a day since 10-22 | Margin / win: the 10 base columns. Total: 10 + `adj_epa` block (4 columns). Props: 6 rolling columns | `epa` and `priors` blocks for margin/win; advanced stats beyond adj EPA; rosters (props only) | **CFBD betting lines** (open + close spread/total by book, 2013+, Pre); returning production, talent composite, transfer portal (Pre); CFBD pregame win probabilities (Pre) |
| **NBA** | ESPN boxscores + player boxes for 10,076 games, 1,912 scoreboards; `nba_history.json.gz`; training games; injury report daily since 10-20 (repo); one sportsbook snapshot (37 events); Kalshi game lines + **props** twice a day since 10-15 (largest settled set: ~39k prop contracts) | 25 columns: four factors x8 (rolling), Elo diff, adjusted off/def x4, pace x2, rest / back-to-back x4, streak x2, altitude, conference, division, lagged rotation availability. Props: rolling player stats + opponent-vs-position, rest, usage/minutes trend, home | travel/fatigue (lost its eval), injury report as an input (#177 negative), `game_context` columns, **Kalshi props never compared with the served props models** | nba_api play-by-play, lineups, on/off (post-game, so lagged); official injury report PDFs, 2021+ history (Pre); referee assignments (Pre, morning of game); SBR opening/closing archives (Pre) |
| **PL** | football-data 2014-2027: results, shots, SoT, corners, cards, referee, and **1X2 open + close for ~6 books (Pinnacle to mid 2025-26), Betfair exchange (2025-26+), O/U 2.5 and Asian handicap open + close**; Understat team xG 2014+; Understat shots for 4,655 matches; FPL history 2016+, FPL players (current), FPL events; cups/UCL calendar; one sportsbook snapshot (21); Kalshi 1X2 / total / BTTS twice a day since 10-18 | `ml_scoreline`: 231 columns: rolling goals/shots/SoT/corners/cards/points (3/5/10, home/away splits), Understat xG 5/10 and xG delta, Elo, pi-ratings, h2h, rest (with cups), squad continuity (FPL), referee card rate, first-match flag | shot-level xG (game state, big chances); `fixture_congestion`, `match_dominance`, `shot_situation`, `player_form` built but not served; **O/U 2.5 and AH lines never compared with the model**; open-to-close moves | confirmed lineups (Pre, about 1 h before kickoff); FPL `chance_of_playing` (Pre, but no history unless snapshotted); ClubElo (cache empty, Elo is computed in-house); FBref/Opta xG (scraping restricted) |
| **F1** | **FastF1 2022-2026, 11 GB (laps, telemetry, every session including FP1-3)**; Jolpica results + qualifying 2019+ (380 files); Open-Meteo weather; OpenF1 cache empty; Kalshi pole / top-10 / win / podium twice a day since 10-22 | Race ranker: 23 columns (Elo, team strength, form 3/5/10, circuit history, grid, quali position, gap to pole, weather) + grid calibration layer (#95, shipped). Session tier `POST_PRACTICE: []` (planned `fp_best_lap_rank`, `fp_long_run_pace_delta`, `fp_tyre_deg_estimate`, never built) | **all practice-session data**; telemetry (`energy.py` exists, not served); live models are not market-compared | OpenF1 live timing and positions (Pre for qualifying once FP has run) |
| **Hub paper ledger** | `ledger.jsonl`: 3,334 candidates (YES only), 1,312 score rows, 3,894 settles; `closing.jsonl` 93 rows; `report.json` | n/a | no NO-side rows, no prop rows, no PL / F1 candidates; the model-vs-price drift (probe B) was never measured | n/a |

Pattern: every repo collects the market (Kalshi, twice a day) and the served models never see or test against
most of it. Game-level inputs are well covered. Props, practice sessions and price paths are not.

## Part 2. Angles, ranked by expected value

EV = plausibility that the market is beatable there x how much is tradeable x how cheap the test is. Every test
below follows the ship rule: chronological walk-forward, paired bootstrap 95% CI resampling games/races/matches,
the primary metric's CI must exclude zero in the model's favour, no other reported metric worse, raw output stored
beside corrected. Each test is pre-registered before its data accrues, and no threshold is tuned on the evaluation window.

### 1. Line movement and timing (model as an early signal)

- **Hypothesis.** The model reads public information the price absorbs only by the close. So the model is worth
  something *early*, even though it loses to the *closing* price on outcomes.
- **Data.** Have: ledger first-log `model_p`, Kalshi snapshots twice a day, PL Pinnacle open/close 2014-2025. Need:
  nothing for the live test. Tighter timing needs more frequent snapshots (hourly on game days).
- **Pre-registered test.** From registration day forward, every ledger market first logged at least 24 h before start.
  Metric: CLV = sign(model_p - p0) x (p_close - p0), where p_close = the last snapshot at least 5 h before Kalshi
  `occurrence` (or a real start time once wired in). Primary: mean CLV in markets with |model_p - p0| >= 0.05 has a
  game-clustered 95% CI **above the round-trip cost** (taker fee at p0 + half spread; about 2.75c at 50c). Secondary:
  same with the maker fee (a quarter of taker). Split: the first 4 weeks after registration, untouched until then.
  Second test: a market-relative regression (predict p_close - p0 from d, lead time, liquidity), walk-forward by week.
- **Cheap probe.** Done (probe B, probe A below). Positive, but below taker cost except CFB moneyline.
- **Cost.** S: one script on the existing ledger, plus an hourly collector if the signal holds.

### 2. Player props (NFL, NBA)

- **Hypothesis.** Prop markets are thinner (NBA median spread 4c vs 2c for game lines), and our props models have
  never been scored against them. Thin markets are where a model with real player-level inputs can be ahead.
- **Data.** Have: served props models (NFL 14 columns + ZINB receptions, NBA points/rebounds/assists/threes), Kalshi
  props with strike, player and settlement. Need: a player-name to id join and a P(stat >= strike) from each model's
  distribution (the NFL quantile/ZINB models already give one).
- **Pre-registered test.** Log every prop as a shadow row (no stake) for 4 weeks. Primary: per-contract log loss of
  the model vs the Kalshi mid, paired, clustered by game; then a walk-forward blend weight w fitted on earlier weeks
  only (the anchor-study rule). An edge needs w > 0 with a CI, and the blend's log loss must beat the mid with a CI
  excluding 0.
- **Cheap probe.** Calibration of the mids (probe C): calibrated, so any edge must come from the model. Next probe:
  backfill model probabilities for the ~39k settled NBA prop contracts already on disk.
- **Cost.** S-M (the join is the work).

### 3. F1 practice pace for qualifying and race markets

- **Hypothesis.** Kalshi pole and top-10 markets are priced before qualifying. FP long-run and single-lap pace
  predicts qualifying better than season form, and none of it is in the model.
- **Data.** Have: FastF1 laps for every session 2022-2026, Kalshi F1 markets. Need: FP feature extraction (best
  lap rank, long-run pace delta, deg), already specified in `session_state.py`.
- **Pre-registered test.** Quali ranker walk-forward by race 2023-2026 (train on earlier races only). Primary:
  pole/top-10 log loss, with vs without FP features, paired bootstrap over races. Then a shadow comparison against
  Kalshi pre-quali prices.
- **Cheap probe.** F1 YES longshots look overpriced (13 events). Not enough to say anything.
- **Cost.** M (FastF1 parsing is the work; data is local).

### 4. NO side of multi-outcome markets

- **Hypothesis.** On 3-way (PL) and many-runner (F1) Kalshi markets, the YES legs together are priced above 1
  (overround sits on YES), so NO is systematically cheap.
- **Data.** Have, accumulating twice a day.
- **Pre-registered test.** From registration on: per contract, net return of buying NO at the ask (after fee), by
  market class, clustered by event, Bonferroni over classes. Ship as a paper rule only if the CI excludes 0 after 100 events.
- **Cheap probe.** Probe C: PL 1X2 NO +5.5c [-0.1, +10.8] (35 matches). F1 NO -1.4c. NBA/CFB game NO -2.5 to -2.9c. Inconclusive.
- **Cost.** S, but needs time (about one PL season for 100 events).

### 5. CFB historical lines (CFBD)

- **Hypothesis.** CFB is the least efficient US market (G5 games, 130+ teams), and our live CFB moneyline drift
  is the largest. With line history we can test angles 1 and 6 on 10+ seasons instead of one month.
- **Data.** Need: CFBD `/lines` (free key), opening and closing spread/total per book.
- **Pre-registered test.** Rerun the anchor-study protocol (model vs close, walk-forward blend, segments
  power/G5) and probe A (model vs open, then the move to close) on 2014-2025.
- **Cost.** S (one pull; the anchor harness is reusable).

### 6. Totals

- **Hypothesis.** NFL total is the only market where the blend gave the model w > 0 (0.07-0.18). PL O/U 2.5
  lines are on disk and never compared with the scoreline model's P(over).
- **Test.** PL: walk-forward P(over 2.5) vs Pinnacle O/U close, paired log loss by match. NFL: as in the anchor study, restricted to totals.
- **Cost.** S.

### 7. Kalshi vs sportsbook consensus

- **Hypothesis.** Kalshi lags books, and the gap settles toward the books.
- **Data.** Need: `sportsbook_api.py` (multi-book, including Kalshi/Polymarket) ran once on 10-14. Schedule it with the Kalshi collector.
- **Test.** Per market: sign(q_books - q_kalshi) x (y - q_kalshi), clustered by game; then the same on CLV.
- **Probe.** The PL proxy (sharp venue vs consensus) finds nothing. Low prior.
- **Cost.** S to collect, then months to accrue.

Segments the anchor study did not test, worth registering inside angles 1-2: price level (longshots < 10c; probe C
shows NBA game longshots settling +2.1 pts above mid [+0.2, +4.2]), liquidity/volume tercile, lead time in hours
(continuous, not the 24 h split), and day of week (CFB Saturday slate size).

## Part 3. Probes (exploratory)

Common: Kalshi snapshots = every `data/lines/*.jsonl.gz` on the five site repos' origin/main (157 files, 125,463
tickers, 115,561 settled). Kalshi `occurrence` is the expected *end* of the event (NFL kickoff + 3 h), so
"pre-start" = the last two-sided quote at least 5 h before `occurrence`. Mid = (bid + ask) / 2. Fees use the hub's
`paper.fees.kalshi_fee_cents` at 100 contracts. CIs use the hub's `paper.stats.cluster_ci` (2,000 draws, whole games
resampled; Kalshi game = the event code shared by a game's line and prop markets).

### Probe B: does the model's disagreement predict the Kalshi move? (live, 10-15 to 11-17)

First ledger log per ticker (candidate or score row): p0 = mid at that snapshot, d = model_p - p0, p1 = last
pre-start mid, at least 6 h after p0. Statistic = sign(d) x (p1 - p0), in probability points (pts).

| Cell | Markets | Games | Model-side CLV, pts [95% CI] |
|---|---|---|---|
| All | 2,379 | 560 | **+0.73 [+0.42, +1.05]** |
| Control: fade the favourite (sign(0.5 - p0)) | 2,379 | 560 | -0.20 [-0.45, +0.04] |
| Model side agrees with fade-favourite | 1,313 | 464 | +0.48 [+0.13, +0.86] |
| Model side **opposes** fade-favourite | 1,066 | 405 | **+1.05 [+0.63, +1.47]** |
| \|d\| < 0.05 | 273 | 196 | +0.16 [-0.51, +0.80] |
| \|d\| 0.05-0.10 | 838 | 353 | +0.59 [+0.17, +1.00] |
| \|d\| >= 0.10 | 1,268 | 349 | +0.95 [+0.50, +1.42] |
| cfb:moneyline | 275 | 250 | +2.99 [+1.90, +4.09] |
| nba:moneyline | 168 | 159 | +2.28 [+1.04, +3.46] |
| cfb:total | 701 | 206 | +0.57 [+0.09, +1.07] |
| cfb:spread | 736 | 211 | +0.15 [-0.45, +0.70] |
| nfl:total | 223 | 43 | -1.05 [-2.17, -0.06] |

Mean absolute move is 3.5 pts. Prices drift toward the model, more so the bigger the disagreement, and plain mean
reversion does not explain it. This sits alongside the anchor result: at |d| >= 0.10 the model is on the wrong side
of the *outcome* 58% of the time. Reading: the model carries some early information the market absorbs by the close,
and the close then knows more than the model. Most cells are below a taker round trip (~2.75c). Caveats: ledger
rows are a YES-only selection (`candidate`) plus `score` rows; one month; cells chosen after seeing the data.

### Probe A: same question on PL history (Pinnacle "open" to close, 2021-22 to 2025-26)

Model = the anchor study's walk-forward `pl_oof.csv.gz`. "Open" = football-data's earlier Pinnacle price
(collected days before; not the true opening). Rows = home and away outcomes, clustered by match.

| Cell | Rows | Matches | Model-side CLV, pts [95% CI] |
|---|---|---|---|
| All | 3,460 | 1,730 | **+0.19 [+0.08, +0.30]** |
| Control: back the underdog | 3,460 | 1,730 | -0.06 [-0.18, +0.06] |
| Model side opposes fade-favourite | 1,288 | 724 | +0.33 [+0.14, +0.52] |
| \|d\| >= 0.10 | 530 | 384 | +0.47 [+0.18, +0.77] |

Same sign as live, about a quarter of the size (Pinnacle moves 2.0 pts on average). Not tradeable at Pinnacle
margins. It is out-of-sample (walk-forward), so it is the stronger evidence that the direction is real.

### Probe C: which Kalshi side is overpriced? (last pre-start quote vs settlement)

| Class | Contracts | Games | Median spread | Outcome - mid, pts | YES at ask, net c | NO at ask, net c |
|---|---|---|---|---|---|---|
| All | 90,195 | 876 | 0.02 | -0.15 [-0.89, +0.59] | -3.65 [-4.36, -2.90] | -3.29 [-4.08, -2.51] |
| NBA props | 38,998 | 180 | 0.04 | +0.49 [-0.42, +1.48] | -3.97 [-4.84, -3.06] | -4.90 [-6.04, -3.83] |
| NFL props | 18,142 | 71 | 0.02 | -0.87 [-2.58, +1.00] | -3.13 [-4.85, -1.27] | -1.34 [-3.21, +0.39] |
| CFB game | 18,045 | 538 | 0.02 | -1.13 [-2.75, +0.54] | -4.80 [-6.45, -3.09] | -2.47 [-4.23, -0.80] |
| NBA game | 13,035 | 219 | 0.02 | +0.66 [-1.47, +2.68] | -1.63 [-3.74, +0.39] | -2.91 [-4.92, -0.78] |
| NFL game | 1,302 | 62 | 0.01 | -1.30 [-8.37, +5.33] | -3.13 [-10.21, +3.50] | -0.52 [-7.23, +6.53] |
| PL game | 345 | 35 | 0.01 | **-7.34 [-12.63, -1.83]** | -9.16 [-14.46, -3.63] | +5.53 [-0.05, +10.80] |
| F1 | 328 | 13 | 0.04 | -2.37 [-3.82, -0.48] | -6.30 [-9.07, -4.22] | -1.38 [-2.69, -0.87] |

Kalshi mids are calibrated in every high-volume class. Taking either side at the ask loses roughly the fee plus
half the spread, so blind side rules do not work. The exceptions are the two multi-outcome classes (PL, F1), where
YES settles below mid. That is angle 4, and n is tiny. Price-band cells are in the log (e.g. NBA game < 10c: +2.09 pts [+0.20, +4.15]).

### Probe D: does the outcome follow a sharp venue that disagrees with consensus? (PL proxy for angle 7)

| Venue vs market-average close | Rows | Matches | Outcome follows venue, pts | Log-loss gain vs average |
|---|---|---|---|---|
| Pinnacle close, 2014-15 to 2025-26 | 10,890 | 2,490 | +0.26 [-1.01, +1.48] | +0.0001 [-0.0007, +0.0008] |
| Betfair exchange close, 2025-26 to 2026-27 | 3,594 | 818 | -1.12 [-3.33, +1.09] | -0.0011 [-0.0032, +0.0008] |

At the close, an exchange (or sharp book) that disagrees with the consensus is no better than the consensus. This is
weak evidence against angle 7. It is only a proxy: Kalshi vs US books needs collected data.

### What would make these real tests

Register before the data accrues (date-stamped commit), fix the window (e.g. the 4 weeks after registration), use
true start times instead of `occurrence - 5 h`, one primary cell per angle, Bonferroni for any others, and for
probes B and C compare against the actual round-trip cost, not zero. Probe A is already out-of-sample, but
"open" is football-data's Friday price; a real test needs a true opening price.

## Reproduce

```
L=<dir>; for r in NBA NFL CFB PL F1; do (cd <Projects>/${r}_Predictor && for f in $(git ls-tree -r --name-only origin/main data/lines); do git show origin/main:$f > $L/$(basename $f); done); done
python3 scripts/research/data_angles_probes.py --selftest
python3 scripts/research/data_angles_probes.py $L <Projects>/PL_Predictor/data/cache/football_data > scripts/research/data_angles_probes.log
```
