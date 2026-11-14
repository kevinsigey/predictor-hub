# AIS-1 — does the summary restate the fixture detail?

Status: audit only. No service code changed. Raw responses are beside this file
in `samples.json`; the restatement tally is `restate.json`.

## Method

- Read `services/explainer/explainer/prompts.py`, `template.py`, `validate.py`
  and `news.py` on `origin/main` (`e4e0693`).
- Stood up the five sport APIs locally and the explainer against them on
  2026-10-27. Pulled five fixtures per sport from `GET /facts/upcoming` (F1:
  rounds 19–23; CFB: the first five upcoming games).
- Called the explainer's own `template.explain_from_template` on each live
  `/facts` bundle. This is the exact no-model path the service serves when the
  model is unavailable or its answer is rejected.
- **Limit:** the configured OpenRouter key answered `401 User not found`, so
  every sample is `"source": "template"`. The LLM path could not be sampled.
  The audit therefore measures the served fallback, and reads the prompt and
  validator for the LLM path rather than pretending to measure it.

Reproduce:

```bash
# sport APIs on 8010 (NBA), 8011 (PL), 8012 (NFL), 8013 (CFB), 8014 (F1)
cd services/explainer
PYTHONPATH=. python - <<'PY'
import json, urllib.request
from explainer.template import explain_from_template
f = json.load(urllib.request.urlopen("http://127.0.0.1:8011/facts/OVT3-AkFm-y0Mv"))
print(explain_from_template(f)["read"])
PY
```

## What the code already does

**Prompt (`prompts.py`).** The frame already says the reader sees the pick,
probability, spread, total, moneyline and rest days "on the same screen", and
forbids writing them. It points the model at `context.matchups`,
`context.player_context`, `context.availability` and `context.form_rows`.

**Template (`template.py`).** `tactical_read()` emits only matchup ranks, named
players and form rows. `explain_from_template()`'s verdict is deliberately just
`"{label} is the pick."` — the one sentence that names the headline.

**Validator (`validate.py`).** `_restated_figures()` separates market/pick/rest
figures from tactical figures and rejects a read that cites a market figure and
not a tactical one. Its own docstring records the ceiling: a rank `#3` and a
spread of `3` are indistinguishable by value, so a real rank can trip the
check or a real spread can hide behind one.

## Results

Scope: the metric counts repeats of pick, market and rest figures only. The Matchup section also displays the rank rows that `tactical_read()` cites, so rank comparisons in a read do repeat displayed information; those are not tallied here, so a 0% below does not mean the read adds no new matchup information.

| sport | reads | read sentences | sentences that restate a pick, market or rest figure | restate-rate |
|---|---:|---:|---:|---:|
| PL | 5 | 10 | 0 | 0% |
| F1 | 5 | 5 | 0 | 0% |
| NBA | 5 | 8 | 0 | 0% |
| NFL | 5 | 14 | 0 | 0% |
| CFB | 5 | 0 | 0 | n/a (no read) |
| **total** | **25** | **37** | **0** | **0%** |

The naive numeric scan in `restate.json` flags three sentences (`#3`, `#3`,
`#4`) because those ranks happen to equal a market figure elsewhere in the same
bundle. Manual review rejects all three as false positives: they are rank
comparisons, not restatements. This is the documented value-collision ceiling,
not evidence that the reads restate.

The headlines above the reads **do** restate by design: 20 of 20 verdicts with a
pick name that pick ("Man United win is the pick.", "Norris is the pick."), and
the five no-pick NBA verdicts say "There is no pick for this one yet." That is
the only displayed field in the prose.

## Per-sport finding

- **PL** — the strongest case. The read is an attack-vs-defence comparison with
  ranks and a form window. It is already the shape AIS-3 asks for.
- **F1** — the read is entirely about the pick's driver (recent form, qualifying
  pace, track history). Contextual, but one-sided: no rival's numbers appear.
- **NBA** — contextual duel ranks, but the local bundle has no pick or markets,
  so the service shows "There is no pick". The read still names two units.
- **NFL** — contextual duel ranks plus named players. Best coverage.
- **CFB** — **no `context.matchups` at all** on every sampled bundle, so
  `tactical_read()` returns `""`. The reader gets a verdict and no read. This is
  the largest gap and AIS-2 must fill it.

## News

`news.py` fetches `site.api.espn.com` free JSON for all five sports, caches for
30 minutes (5 minutes after a failure), keeps four newest whole-word team or
driver matches, and returns `[]` on any error. `service.py` calls
`headlines()` before the model call and passes the result as `NEWS`; the cache
key includes the newest headline date. **It is wired in.**

Reliability, measured 2026-10-27: all five ESPN endpoints answered `200` with
17–31 KB. No paid API is needed and none should be added.

Two matching gaps, not fetch gaps:

- `TEAM_NAMES` has NFL and NBA only. CFB matches bare abbreviations like
  `ALA`/`UGA` against headlines that say "Alabama"/"Georgia", so most CFB
  headlines are missed. A CFB nickname table is the fix.
- F1 matches driver surnames from `drivers[:3]`; that is adequate for
  "Norris"/"Leclerc" but will miss team-level stories.

Recommendation: keep ESPN, add the CFB nickname table, do not add a news
source in AIS-2/AIS-3.

## What AIS-2 and AIS-3 must change

1. **CFB:** emit `context.matchups` (offence vs opponent defence, rank, gap) or
   the summary has nothing to say.
2. **Every sport:** add the attack/defence ratings the fixture detail does not
   currently show, each with a `source` field, and keep the validator's number
   pool as the gate.
3. **Prompt:** lead with the matchup story. The current frame forbids the
   numbers but does not forbid a qualitative restatement ("the model likes X").
4. **Validator:** add a sentence-level restatement check for the pick label and
   for a paraphrase of the probability, not just a numeric-token check. Keep
   the existing sign/verdict/clause rules untouched.
