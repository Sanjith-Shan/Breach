"""The statistics are hand written, so they are checked against known values."""

import math

from breach import stats


def test_wilson_matches_published_values():
    # Wilson interval for 0/36 and 18/36 at 95%.
    lo, hi = stats.wilson(0, 36).lo, stats.wilson(0, 36).hi
    assert lo == 0.0
    assert abs(hi - 0.0961) < 0.001
    i = stats.wilson(18, 36)
    assert abs(i.point - 0.5) < 1e-12
    assert abs(i.lo - 0.3449) < 0.001 and abs(i.hi - 0.6551) < 0.001


def test_wilson_is_empty_on_no_data():
    i = stats.wilson(0, 0)
    assert math.isnan(i.point)


def test_fisher_exact_matches_reference():
    # scipy.stats.fisher_exact([[10, 0], [2, 8]]) two sided.
    assert abs(stats.fisher_exact_two_sided(10, 0, 2, 8) - 0.000714) < 1e-5
    assert stats.fisher_exact_two_sided(5, 5, 5, 5) == 1.0
    assert math.isnan(stats.fisher_exact_two_sided(0, 0, 0, 0))


def test_cluster_bootstrap_is_wider_than_wilson_when_clustered():
    """The whole reason the bootstrap is the headline interval.

    Twelve tasks, each perfectly consistent within itself and split half and
    half between tasks. Wilson sees 36 independent draws at 50% and is narrow.
    The bootstrap sees twelve clusters and is much wider, correctly.
    """
    clusters = [[True] * 3] * 6 + [[False] * 3] * 6
    boot = stats.cluster_bootstrap(clusters, seed=1)
    wil = stats.wilson(18, 36)
    assert abs(boot.point - 0.5) < 1e-9
    assert (boot.hi - boot.lo) > (wil.hi - wil.lo)


def test_cluster_bootstrap_is_deterministic_under_a_seed():
    c = [[True, False, True], [False, False, True], [True, True, True]]
    assert stats.cluster_bootstrap(c, seed=7) == stats.cluster_bootstrap(c, seed=7)


def test_cluster_bootstrap_insufficient_clusters():
    i = stats.cluster_bootstrap([[True, True]], seed=0)
    assert i.point == 1.0
    assert math.isnan(i.lo) and math.isnan(i.hi)
    assert "insufficient" in i.method


def test_cluster_bootstrap_empty_is_nan():
    i = stats.cluster_bootstrap([], seed=0)
    assert math.isnan(i.point)
    assert i.n == 0


def test_paired_bootstrap_recovers_a_known_difference():
    pairs = [([True] * 3, [False] * 3)] * 8
    d = stats.paired_bootstrap_diff(pairs, seed=3)
    assert abs(d.point - 1.0) < 1e-9


def test_paired_bootstrap_insufficient_pairs():
    d = stats.paired_bootstrap_diff([([True], [False])], seed=0)
    assert math.isnan(d.lo) and math.isnan(d.hi)


def test_intervals_are_bounded():
    for k in range(0, 21):
        i = stats.wilson(k, 20)
        assert 0.0 <= i.lo <= i.point <= i.hi <= 1.0


def test_interval_pct_formats_as_percentage():
    i = stats.wilson(18, 36)
    s = i.pct(1)
    assert s.startswith("50.0%")
    assert "[" in s and "]" in s


def test_group_helper():
    rows = [{"k": "a", "v": 1}, {"k": "a", "v": 2}, {"k": "b", "v": 3}]
    g = stats.group(rows, key=lambda r: r["k"], value=lambda r: r["v"])
    assert g == {"a": [1, 2], "b": [3]}
