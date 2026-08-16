"""Statistics, with no third-party dependency.

Three deliberate choices are worth stating because the prior eval in this
family reported bare pass rates and this one does not.

**Trials within a task are not independent.** Three trials on the same task
share the same repo, the same ticket, and the same trap. Treating thirty such
trials as thirty independent Bernoulli draws produces an interval that is too
narrow, sometimes by a lot. So the headline interval here is a **cluster
bootstrap that resamples tasks**, not trials. The Wilson interval is still
reported next to it, labelled as the naive one, because it is what most eval
write-ups quote and a reader deserves to see how much the honest interval
widens.

**Trap and control are paired.** Each trap task has a control twin built from
the same repo with the same patch, so the right comparison resamples matched
pairs rather than two independent groups.

**Everything is seeded.** Bootstrap resamples come from a ``random.Random``
seeded per call, so the same results file produces the same intervals.
"""

from __future__ import annotations

import dataclasses
import math
import random
from collections.abc import Sequence

Z95 = 1.959963984540054


@dataclasses.dataclass(frozen=True)
class Interval:
    point: float
    lo: float
    hi: float
    method: str
    n: int

    def pct(self, digits: int = 1) -> str:
        f = f"{{:.{digits}f}}"
        return (f"{f.format(100 * self.point)}% "
                f"[{f.format(100 * self.lo)}, {f.format(100 * self.hi)}]")


def wilson(successes: int, n: int, z: float = Z95) -> Interval:
    """Wilson score interval. Assumes independent trials, which is why it is
    reported as the naive companion to the cluster bootstrap and not alone."""
    if n == 0:
        return Interval(float("nan"), float("nan"), float("nan"), "wilson", 0)
    p = successes / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return Interval(p, max(0.0, centre - half), min(1.0, centre + half), "wilson", n)


def cluster_bootstrap(clusters: Sequence[Sequence[bool]], seed: int = 0,
                      resamples: int = 10000, alpha: float = 0.05) -> Interval:
    """Percentile bootstrap over clusters.

    ``clusters`` is one sequence of trial outcomes per task. Tasks are drawn
    with replacement; every trial inside a drawn task comes along with it.
    """
    flat = [x for c in clusters for x in c]
    if not flat:
        return Interval(float("nan"), float("nan"), float("nan"),
                        "cluster-bootstrap", 0)
    point = sum(flat) / len(flat)
    if len(clusters) < 2:
        return Interval(point, float("nan"), float("nan"),
                        "cluster-bootstrap(insufficient clusters)", len(flat))
    rng = random.Random(seed)
    idx = range(len(clusters))
    draws = []
    for _ in range(resamples):
        pick = [clusters[rng.choice(idx)] for _ in idx]
        vals = [x for c in pick for x in c]
        if vals:
            draws.append(sum(vals) / len(vals))
    draws.sort()
    lo = draws[int(alpha / 2 * len(draws))]
    hi = draws[min(len(draws) - 1, int((1 - alpha / 2) * len(draws)))]
    return Interval(point, lo, hi, f"cluster-bootstrap(n_clusters={len(clusters)})",
                    len(flat))


def paired_bootstrap_diff(pairs: Sequence[tuple[Sequence[bool], Sequence[bool]]],
                          seed: int = 0, resamples: int = 10000,
                          alpha: float = 0.05) -> Interval:
    """CI for (rate in arm A) minus (rate in arm B), resampling matched pairs.

    Each element of ``pairs`` is one matched unit, for example a mechanism with
    its trap trials and its control trials.
    """
    def rate(sel):
        a = [x for p in sel for x in p[0]]
        b = [x for p in sel for x in p[1]]
        if not a or not b:
            return None
        return sum(a) / len(a) - sum(b) / len(b)

    point = rate(pairs)
    if point is None or len(pairs) < 2:
        return Interval(point if point is not None else float("nan"),
                        float("nan"), float("nan"), "paired-bootstrap", len(pairs))
    rng = random.Random(seed)
    idx = range(len(pairs))
    draws = []
    for _ in range(resamples):
        r = rate([pairs[rng.choice(idx)] for _ in idx])
        if r is not None:
            draws.append(r)
    draws.sort()
    lo = draws[int(alpha / 2 * len(draws))]
    hi = draws[min(len(draws) - 1, int((1 - alpha / 2) * len(draws)))]
    return Interval(point, lo, hi, f"paired-bootstrap(n_pairs={len(pairs)})",
                    len(pairs))


def fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact test on the 2x2 table [[a, b], [c, d]].

    Exact rather than chi-square because several cells in this suite are small
    or zero, which is exactly where the approximation misleads.
    """
    n = a + b + c + d
    if n == 0:
        return float("nan")
    r1, r2, c1 = a + b, c + d, a + c

    def hyper(k: int) -> float:
        if k < max(0, c1 - r2) or k > min(r1, c1):
            return 0.0
        return (math.comb(r1, k) * math.comb(r2, c1 - k)) / math.comb(n, c1)

    observed = hyper(a)
    lo, hi = max(0, c1 - r2), min(r1, c1)
    # Sum every table at least as extreme as the observed one, with a
    # tolerance so floating point does not silently drop the observed table.
    total = sum(p for k in range(lo, hi + 1)
                if (p := hyper(k)) <= observed * (1 + 1e-9))
    return min(1.0, total)


def group(rows, key, value) -> dict:
    out: dict = {}
    for r in rows:
        out.setdefault(key(r), []).append(value(r))
    return out
