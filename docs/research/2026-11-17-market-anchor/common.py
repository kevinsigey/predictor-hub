"""Shared bits for the market-anchor study: paired bootstrap, walk-forward blend weight, Wilson interval.

Evidence scripts, not hub code; nothing imports them. Run `python common.py` for the self-check.
"""
import math
import numpy as np

GRID = np.round(np.arange(0, 1.0001, 0.01), 2)


def boot(d, n=2000, seed=0, alpha=0.05):
    """Mean of paired per-game differences and its percentile bootstrap interval (resampling games)."""
    d = np.asarray(d, float)
    if len(d) == 0:
        return float("nan"), float("nan"), float("nan")
    m = d[np.random.default_rng(seed).integers(0, len(d), (n, len(d)))].mean(1)
    return float(d.mean()), float(np.percentile(m, 100 * alpha / 2)), float(np.percentile(m, 100 * (1 - alpha / 2)))


def logit(p):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def blend_p(q, p, w):
    """logit b = logit q + w (logit p - logit q)."""
    return 1 / (1 + np.exp(-(logit(q) + w * (logit(p) - logit(q)))))


def ll(p, y):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def best_w(loss_of_w):
    """Grid argmin over w in [0, 1]; ties go to the smaller w (closer to the line)."""
    losses = [loss_of_w(w) for w in GRID]
    return float(GRID[int(np.argmin(losses))])


def walk_forward_w(period, loss_of_w_on, min_periods=3):
    """{period: w} where w for period s is fitted only on rows from periods < s.

    `loss_of_w_on(mask, w)` returns the mean loss of the blend on the rows in `mask`.
    w = 0 (pure line) until `min_periods` earlier periods exist.
    """
    period = np.asarray(period)
    out = {}
    for s in sorted(set(period.tolist())):
        earlier = period < s
        if len(set(period[earlier].tolist())) < min_periods:
            out[s] = 0.0
        else:
            out[s] = best_w(lambda w: loss_of_w_on(earlier, w))
    return out


def wilson(k, n, z=1.96):
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return c - h, c + h


def z_for(alpha):
    """Two-sided normal quantile without scipy (bisection on erf)."""
    lo, hi = 0.0, 10.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if math.erfc(mid / math.sqrt(2)) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


if __name__ == "__main__":
    rng = np.random.default_rng(1)
    line = rng.normal(0, 5, 4000)
    truth = line + rng.normal(0, 1, 4000)          # truth = line + a little signal the model sees
    model = truth + rng.normal(0, 1, 4000)
    actual = truth + rng.normal(0, 3, 4000)
    season = np.repeat(np.arange(8), 500)
    ws = walk_forward_w(season, lambda m, w: np.abs(line[m] + w * (model[m] - line[m]) - actual[m]).mean())
    assert ws[0] == ws[1] == ws[2] == 0.0 and 0.3 < ws[7] < 0.7, ws
    assert boot(np.ones(10))[1:] == (1.0, 1.0)
    assert abs(z_for(0.05) - 1.95996) < 1e-3
    lo, hi = wilson(55, 100); assert 0.45 < lo < 0.46 and 0.64 < hi < 0.65
    assert np.allclose(blend_p(0.3, 0.7, 0), 0.3) and np.allclose(blend_p(0.3, 0.7, 1), 0.7)
    print("common.py self-check OK", ws[7])
