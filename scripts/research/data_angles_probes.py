#!/usr/bin/env python3
"""Exploratory probes for docs/research/2026-11-18-data-coverage-and-angles.md. Research only, stdlib only.

  python3 scripts/research/data_angles_probes.py LINES_DIR PL_FOOTBALL_DATA_DIR
  python3 scripts/research/data_angles_probes.py --selftest

LINES_DIR: every data/lines/*.jsonl.gz from the five site repos' origin/main (Kalshi snapshots, ~2 a day).
PL_FOOTBALL_DATA_DIR: PL_Predictor/data/cache/football_data (football-data.co.uk CSVs).
Model probabilities come from files already committed in this repo: data/paper/ledger.jsonl (live) and
docs/research/2026-11-17-market-anchor/pl_oof.csv.gz (PL walk-forward out-of-fold).

Every CI is the hub's paper.stats.cluster_ci (percentile bootstrap, 2,000 draws, games as clusters).
"""
import csv
import glob
import gzip
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "services", "paper"))
from paper.fees import kalshi_fee_cents  # noqa: E402
from paper.stats import cluster_ci  # noqa: E402

# Kalshi `occurrence` is the expected END of the event (NFL: kickoff + 3 h, NBA: tip + ~2.5 h), so a snapshot at
# least 5 h before it is pre-start for every sport here. ponytail: fixed offset, swap for real start times if a
# probe graduates to a test.
PRESTART = timedelta(hours=5)


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fee(p):
    """Per-contract taker fee in probability units, at 100 contracts (avoids one-contract cent rounding)."""
    return kalshi_fee_cents(p * 100, contracts=100) / 100 / 100


def ci(groups):
    r = cluster_ci(groups, by_row=True)
    return f"{r['mean']:+.4f} [{r['ci'][0]:+.4f}, {r['ci'][1]:+.4f}]" if r else "n/a"


def devig(*odds):
    inv = [1 / float(o) for o in odds]
    return [x / sum(inv) for x in inv]


def bucket(x, edges):
    for lo, hi in zip(edges, edges[1:]):
        if lo <= x < hi:
            return f"{lo:.2f}-{hi:.2f}"
    return f">={edges[-1]:.2f}"


def load_kalshi(lines_dir):
    """ticker -> {'sport', 'stat', 'player', 'game', 'occ', 'result', 'snaps': [(ts, bid, ask, no_ask)]}"""
    out = {}
    for f in sorted(glob.glob(os.path.join(lines_dir, "kalshi_*.jsonl.gz"))):
        sport = os.path.basename(f).split("_")[1]
        for line in gzip.open(f, "rt"):
            r = json.loads(line)
            t = out.setdefault(r["ticker"], {
                "sport": sport, "stat": r["stat"], "player": r["player"], "occ": r["occurrence"],
                "game": sport + "|" + r["event_ticker"].split("-", 1)[-1], "result": None, "snaps": []})
            if r["result"] in ("yes", "no"):
                t["result"] = r["result"]
            if r["status"] == "active" and r["yes_bid"] and r["yes_ask"] and 0 < r["yes_bid"] < r["yes_ask"] < 1:
                t["snaps"].append((ts(r["snapshot_ts"]), r["yes_bid"], r["yes_ask"], r["no_ask"]))
    for t in out.values():
        t["snaps"].sort()
    return out


def last_prestart(t):
    if not t["occ"]:
        return None
    cut = ts(t["occ"]) - PRESTART
    pre = [s for s in t["snaps"] if s[0] <= cut]
    return pre[-1] if pre else None


def klass(t):
    if t["sport"] == "f1":
        return "f1"
    if t["player"]:
        return f"{t['sport']}:props"
    return f"{t['sport']}:game"


def probe_kalshi_sides(kal):
    """C: at the last pre-start quote, does the settled outcome sit off the mid, and which side pays at the ask?"""
    print("\n## Probe C: Kalshi settlement vs last pre-start quote (exploratory)")
    rows = defaultdict(lambda: {"cal": defaultdict(list), "yes": defaultdict(list), "no": defaultdict(list),
                                "spr": []})
    edges = [0, 0.10, 0.25, 0.50, 0.75, 0.90]
    for t in kal.values():
        s = last_prestart(t)
        if not s or not t["result"]:
            continue
        _, bid, ask, no_ask = s
        y = 1.0 if t["result"] == "yes" else 0.0
        mid = (bid + ask) / 2
        for key in (klass(t), f"{klass(t)}|{bucket(mid, edges)}", "ALL"):
            g = rows[key]
            g["cal"][t["game"]].append(y - mid)
            g["yes"][t["game"]].append(y - ask - fee(ask))
            g["no"][t["game"]].append((1 - y) - no_ask - fee(no_ask))
            g["spr"].append(ask - bid)
    print("| cell | contracts | games | median spread | outcome - mid | buy YES at ask, net | buy NO at ask, net |")
    print("|---|---|---|---|---|---|---|")
    for key in sorted(rows):
        g = rows[key]
        n = sum(len(v) for v in g["cal"].values())
        if n < 30:
            continue
        spr = sorted(g["spr"])[len(g["spr"]) // 2]
        print(f"| {key} | {n} | {len(g['cal'])} | {spr:.2f} | {ci(g['cal'])} | {ci(g['yes'])} | {ci(g['no'])} |")


def probe_drift(kal, ledger_path):
    """B: does the model's disagreement with the first logged Kalshi price predict the price's move to the last
    pre-start snapshot? Statistic: sign(model - p0) * (p1 - p0), i.e. closing-line value of following the model."""
    print("\n## Probe B: model disagreement -> Kalshi drift to last pre-start snapshot (exploratory)")
    first = {}
    for line in open(ledger_path):
        r = json.loads(line)
        if r.get("record") not in ("candidate", "score") or r.get("model_p") is None:
            continue
        if r["ticker"] not in first or r["logged_at"] < first[r["ticker"]]["logged_at"]:
            first[r["ticker"]] = r
    cells = defaultdict(lambda: defaultdict(list))
    for tk, r in first.items():
        t = kal.get(tk)
        if not t:
            continue
        t0 = ts(r["price_observed_at"])
        at0 = [s for s in t["snaps"] if abs((s[0] - t0).total_seconds()) <= 3600]
        s1 = last_prestart(t)
        if not at0 or not s1 or s1[0] - at0[0][0] < timedelta(hours=6):
            continue
        p0 = (at0[0][1] + at0[0][2]) / 2
        p1 = (s1[1] + s1[2]) / 2
        d = r["model_p"] - p0
        if d == 0:
            continue
        v = (1 if d > 0 else -1) * (p1 - p0)
        game = r["sport"] + "|" + str(r["game_id"])
        # Control: "fade the favourite" (sign of 0.5 - p0). The model is shrunk toward 0.5, so its side often
        # coincides with plain mean reversion; the "opposes" cell is the part mean reversion cannot explain.
        c = 1 if p0 < 0.5 else -1
        agree = "agrees with" if (d > 0) == (c > 0) else "opposes"
        keys = ("ALL", f"|d| {bucket(abs(d), [0, 0.05, 0.10])}", f"{r['sport']}:{r['market']}",
                f"model side {agree} fade-favourite")
        for key in keys:
            cells[key][game].append(v)
            cells[key + " (abs move)"][game].append(abs(p1 - p0))
        cells["control: fade-favourite side, ALL"][game].append(c * (p1 - p0))
        cells["control: fade-favourite side, ALL (abs move)"][game].append(abs(p1 - p0))
    print("| cell | markets | games | CLV of model side, prob pts [95% CI] | mean abs move |")
    print("|---|---|---|---|---|")
    for key in sorted(k for k in cells if not k.endswith("(abs move)")):
        g = cells[key]
        a = cells[key + " (abs move)"]
        n = sum(len(v) for v in g.values())
        mabs = sum(sum(v) for v in a.values()) / n
        print(f"| {key} | {n} | {len(g)} | {ci(g)} | {mabs:.4f} |")


def read_fd(fd_dir):
    rows = []
    for f in sorted(glob.glob(os.path.join(fd_dir, "*.csv"))):
        rows += list(csv.DictReader(open(f, encoding="utf-8-sig")))
    return rows


def probe_pl_open_close(fd, oof_path):
    """A: PL walk-forward model vs Pinnacle 'opening' (football-data's earlier collection) -> Pinnacle close."""
    print("\n## Probe A: PL model disagreement with Pinnacle open -> open-to-close move (exploratory)")
    fdk = {(r["date"], r["team_home"], r["team_away"]): r for r in fd}
    cells = defaultdict(lambda: defaultdict(list))
    with gzip.open(oof_path, "rt") as fh:
        for r in csv.DictReader(fh):
            m = fdk.get((r["date"], r["team_home"], r["team_away"]))
            if not m or not all(m.get(k) for k in ("psh", "psd", "psa", "psch", "pscd", "psca")):
                continue
            qo = devig(m["psh"], m["psd"], m["psa"])
            qc = devig(m["psch"], m["pscd"], m["psca"])
            p = [float(r["p_h"]), float(r["p_d"]), float(r["p_a"])]
            game = r["date"] + r["team_home"]
            fav = 0 if qo[0] > qo[2] else 2
            for i in (0, 2):
                d = p[i] - qo[i]
                v = (1 if d > 0 else -1) * (qc[i] - qo[i])
                c = -1 if i == fav else 1  # control: fade the favourite (back the underdog)
                agree = "agrees with" if (d > 0) == (c > 0) else "opposes"
                for key in ("ALL", f"|d| {bucket(abs(d), [0, 0.05, 0.10])}", f"model side {agree} fade-favourite"):
                    cells[key][game].append(v)
                    cells[key + " (abs move)"][game].append(abs(qc[i] - qo[i]))
                cells["control: fade-favourite side, ALL"][game].append(c * (qc[i] - qo[i]))
                cells["control: fade-favourite side, ALL (abs move)"][game].append(abs(qc[i] - qo[i]))
    print("| cell | rows (home+away) | matches | CLV of model side, prob pts [95% CI] | mean abs move |")
    print("|---|---|---|---|---|")
    for key in sorted(k for k in cells if not k.endswith("(abs move)")):
        g, a = cells[key], cells[key + " (abs move)"]
        n = sum(len(v) for v in g.values())
        print(f"| {key} | {n} | {len(g)} | {ci(g)} | {sum(sum(v) for v in a.values()) / n:.4f} |")


def probe_sharp_vs_consensus(fd):
    """D (proxy for Kalshi vs sportsbook consensus): when a sharp venue's closing price deviates from the market
    average close, does the result follow the sharp venue? Statistic per outcome: sign(q_sharp - q_avg) * (y - q_avg)."""
    print("\n## Probe D: sharp venue vs consensus close, PL 1X2 (exploratory proxy)")
    venues = {"pinnacle": ("psch", "pscd", "psca"), "betfair_exchange": ("bfech", "bfecd", "bfeca")}
    print("| venue | cell | rows | matches | outcome follows venue, prob pts [95% CI] | log-loss gain vs avg [95% CI] |")
    print("|---|---|---|---|---|---|")
    import math
    for name, cols in venues.items():
        cells = defaultdict(lambda: defaultdict(list))
        llg = defaultdict(list)
        for r in fd:
            if not all(r.get(k) for k in cols + ("avg_ch", "avg_cd", "avg_ca")) or r["ftr"] not in ("H", "D", "A"):
                continue
            qs = devig(*(r[k] for k in cols))
            qa = devig(r["avg_ch"], r["avg_cd"], r["avg_ca"])
            y = [r["ftr"] == "H", r["ftr"] == "D", r["ftr"] == "A"]
            game = r["date"] + r["team_home"]
            k = y.index(True)
            llg[game].append(math.log(qs[k]) - math.log(qa[k]))
            for i in range(3):
                e = qs[i] - qa[i]
                if e == 0:
                    continue
                v = (1 if e > 0 else -1) * (y[i] - qa[i])
                for key in ("ALL", f"|e| {bucket(abs(e), [0, 0.01, 0.02])}"):
                    cells[key][game].append(v)
        for key in sorted(cells):
            g = cells[key]
            n = sum(len(v) for v in g.values())
            print(f"| {name} | {key} | {n} | {len(g)} | {ci(g)} | {ci(llg) if key == 'ALL' else ''} |")


def selftest():
    assert abs(sum(devig(2.0, 3.5, 4.0)) - 1) < 1e-12
    assert bucket(0.07, [0, 0.05, 0.10]) == "0.05-0.10" and bucket(0.2, [0, 0.05, 0.10]) == ">=0.10"
    assert abs(fee(0.5) - 0.0175) < 1e-9  # 7% * 0.5 * 0.5 = 1.75 c per contract at 100 contracts
    t = {"occ": "2026-11-08T21:00:00Z", "snaps": [(ts("2026-11-08T15:00:00Z"), .3, .32, .7),
                                                   (ts("2026-11-08T18:00:00Z"), .4, .42, .6)]}
    assert last_prestart(t)[1] == .3  # the 18:00 snapshot is inside the 5 h window, so excluded
    print("selftest ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
        sys.exit(0)
    lines_dir, fd_dir = sys.argv[1], sys.argv[2]
    kal = load_kalshi(lines_dir)
    print(f"Kalshi tickers loaded: {len(kal)}; settled: {sum(1 for t in kal.values() if t['result'])}")
    probe_kalshi_sides(kal)
    probe_drift(kal, os.path.join(ROOT, "data", "paper", "ledger.jsonl"))
    fd = read_fd(fd_dir)
    probe_pl_open_close(fd, os.path.join(ROOT, "docs", "research", "2026-11-17-market-anchor", "pl_oof.csv.gz"))
    probe_sharp_vs_consensus(fd)
