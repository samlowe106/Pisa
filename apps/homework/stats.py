"""Dependency-free hypothesis tests for comparing two course sections.

Permutation-based p-values keep this assumption-free and light at the small sample sizes we
deal with (no numpy/scipy). The **primitive** tests are deliberately shaped like their
``scipy.stats`` counterparts — same leading arguments, and a result exposing ``.statistic`` and
``.pvalue`` — so moving to scipy later is close to a drop-in: import the scipy function under
the same name, or swap the body. Each notes its scipy analogue.

The ``compare_*`` helpers and effect sizes are our own orchestration on top of the primitives.

Determinism: permutation p-values are seeded (``DEFAULT_SEED``) so the same data yields the same
number across page loads. Bump ``DEFAULT_RESAMPLES`` for tighter p-values at some CPU cost.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from statistics import fmean
from typing import NamedTuple, Sequence

DEFAULT_RESAMPLES = 5000
DEFAULT_SEED = 1_234_567

Number = float
Sample = Sequence[Number]


class TestResult(NamedTuple):
    """Mirrors the ``(statistic, pvalue)`` shape of scipy.stats result objects."""

    statistic: float
    pvalue: float


def _rng(rng):
    return rng if rng is not None else random.Random(DEFAULT_SEED)


def _sample_var(values, m):
    """Unbiased (ddof=1) variance; 0 for fewer than two values."""
    n = len(values)
    if n < 2:
        return 0.0
    return sum((v - m) ** 2 for v in values) / (n - 1)


# --- Primitives (scipy.stats-shaped) ---------------------------------------------------


def _welch_t(a, b):
    na, nb = len(a), len(b)
    ma, mb = fmean(a), fmean(b)
    denom = _sample_var(a, ma) / na + _sample_var(b, mb) / nb
    if denom <= 0:  # both groups constant
        if ma == mb:
            return 0.0
        return math.inf if ma > mb else -math.inf
    return (ma - mb) / math.sqrt(denom)


def ttest_ind(a, b, *, equal_var=False, n_resamples=DEFAULT_RESAMPLES, rng=None):
    """Welch's t-test with a *permutation* p-value (studentised permutation — robust to
    unequal variance and non-normality at small n). Returns the Welch t and its two-sided
    permutation p-value.

    scipy: ``scipy.stats.ttest_ind(a, b, equal_var=False, permutations=n_resamples)``.
    """
    if equal_var:
        raise NotImplementedError("only Welch's test (equal_var=False) is implemented")
    a, b = list(a), list(b)
    observed = _welch_t(a, b)
    pooled = a + b
    na = len(a)
    rng = _rng(rng)
    extreme = 1  # add-one (Davison & Hinkley): observed counts as one resample
    for _ in range(n_resamples):
        rng.shuffle(pooled)
        if abs(_welch_t(pooled[:na], pooled[na:])) >= abs(observed) - 1e-12:
            extreme += 1
    return TestResult(observed, extreme / (n_resamples + 1))


def _average_ranks(values):
    """1-based ranks with ties broken by the average of the tied positions."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2 + 1  # positions i..j are 1-based ranks i+1..j+1
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return ranks


def mannwhitneyu(x, y, *, n_resamples=DEFAULT_RESAMPLES, rng=None):
    """Mann–Whitney U (rank-sum), tie-corrected, with a two-sided permutation p-value.
    Returns U for ``x``.

    scipy: ``scipy.stats.mannwhitneyu(x, y, alternative='two-sided')``.
    """
    x, y = list(x), list(y)
    nx, ny = len(x), len(y)
    ranks = _average_ranks(x + y)  # the multiset of ranks is fixed under permutation
    observed = sum(ranks[:nx]) - nx * (nx + 1) / 2
    center = nx * ny / 2
    rng = _rng(rng)
    extreme = 1
    for _ in range(n_resamples):
        rng.shuffle(ranks)
        u = sum(ranks[:nx]) - nx * (nx + 1) / 2
        if abs(u - center) >= abs(observed - center) - 1e-12:
            extreme += 1
    return TestResult(observed, extreme / (n_resamples + 1))


def chi2_statistic(table):
    """Pearson χ² statistic for an r×c table of counts."""
    row_totals = [sum(row) for row in table]
    col_totals = [sum(col) for col in zip(*table)]
    n = sum(row_totals)
    if n == 0:
        return 0.0
    chi2 = 0.0
    for i, row in enumerate(table):
        for j, observed in enumerate(row):
            expected = row_totals[i] * col_totals[j] / n
            if expected > 0:
                chi2 += (observed - expected) ** 2 / expected
    return chi2


def chi2_contingency(table, *, n_resamples=DEFAULT_RESAMPLES, rng=None):
    """Pearson χ² for a 2×c table with a permutation p-value (shuffles the row label of the
    underlying individuals) — appropriate when expected cell counts are small.

    scipy: ``scipy.stats.chi2_contingency(table)`` (analytic).
    """
    if len(table) != 2:
        raise NotImplementedError("only two-row (two-section) tables are supported")
    observed = chi2_statistic(table)
    n_cols = len(table[0])
    # Reconstruct individuals as their column index; first na belong to section A.
    individuals = []
    for row in table:
        for col, count in enumerate(row):
            individuals.extend([col] * count)
    na = sum(table[0])
    rng = _rng(rng)
    extreme = 1
    for _ in range(n_resamples):
        rng.shuffle(individuals)
        resampled = [[0] * n_cols, [0] * n_cols]
        for index, col in enumerate(individuals):
            resampled[0 if index < na else 1][col] += 1
        if chi2_statistic(resampled) >= observed - 1e-12:
            extreme += 1
    return TestResult(observed, extreme / (n_resamples + 1))


def false_discovery_control(pvalues, *, method="bh"):
    """Benjamini–Hochberg adjusted p-values (FDR control). Mirrors
    ``scipy.stats.false_discovery_control(ps, method='bh')``."""
    if method != "bh":
        raise NotImplementedError(
            "only the Benjamini–Hochberg ('bh') method is implemented"
        )
    pvalues = list(pvalues)
    m = len(pvalues)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted = [0.0] * m
    running_min = 1.0
    for rank in range(m, 0, -1):  # largest p first
        index = order[rank - 1]
        running_min = min(running_min, pvalues[index] * m / rank)
        adjusted[index] = min(running_min, 1.0)
    return adjusted


# --- Effect sizes (no direct scipy.stats equivalent) -----------------------------------


def cohens_d(a, b):
    """Standardised mean difference using the pooled SD. nan if a group has < 2 values."""
    a, b = list(a), list(b)
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    pooled_var = (
        (len(a) - 1) * _sample_var(a, fmean(a))
        + (len(b) - 1) * _sample_var(b, fmean(b))
    ) / (len(a) + len(b) - 2)
    if pooled_var <= 0:
        return 0.0
    return (fmean(a) - fmean(b)) / math.sqrt(pooled_var)


def cliffs_delta(a, b):
    """Cliff's δ = P(a > b) − P(a < b) — the nonparametric effect size (= rank-biserial r)."""
    a, b = list(a), list(b)
    if not a or not b:
        return float("nan")
    greater = sum(1 for x in a for y in b if x > y)
    less = sum(1 for x in a for y in b if x < y)
    return (greater - less) / (len(a) * len(b))


def cramers_v(table):
    """Cramér's V effect size for a contingency table (0 = no association)."""
    n = sum(sum(row) for row in table)
    if n == 0:
        return float("nan")
    k = min(len(table), len(table[0]))
    if k < 2:
        return 0.0
    return math.sqrt(chi2_statistic(table) / (n * (k - 1)))


# --- Orchestration (Pisa-specific) -----------------------------------------------------


@dataclass
class ScoreComparison:
    n_a: int
    n_b: int
    mean_a: float | None
    mean_b: float | None
    welch: TestResult | None
    mannwhitney: TestResult | None
    cohens_d: float
    cliffs_delta: float

    @property
    def comparable(self):
        return self.welch is not None


def compare_scores(a, b, *, n_resamples=DEFAULT_RESAMPLES, rng=None):
    """Full continuous-score comparison: Welch (permutation) + Mann–Whitney + effect sizes.
    Returns a ``ScoreComparison`` whose tests are ``None`` when a group has < 2 observations.
    """
    a, b = list(a), list(b)
    if len(a) < 2 or len(b) < 2:
        return ScoreComparison(
            n_a=len(a),
            n_b=len(b),
            mean_a=fmean(a) if a else None,
            mean_b=fmean(b) if b else None,
            welch=None,
            mannwhitney=None,
            cohens_d=float("nan"),
            cliffs_delta=cliffs_delta(a, b),
        )
    rng = _rng(rng)
    return ScoreComparison(
        n_a=len(a),
        n_b=len(b),
        mean_a=fmean(a),
        mean_b=fmean(b),
        welch=ttest_ind(a, b, equal_var=False, n_resamples=n_resamples, rng=rng),
        mannwhitney=mannwhitneyu(a, b, n_resamples=n_resamples, rng=rng),
        cohens_d=cohens_d(a, b),
        cliffs_delta=cliffs_delta(a, b),
    )


@dataclass
class LetterComparison:
    n_a: int
    n_b: int
    chi2: TestResult | None
    cramers_v: float

    @property
    def comparable(self):
        return self.chi2 is not None


def compare_letters(counts_a, counts_b, *, n_resamples=DEFAULT_RESAMPLES, rng=None):
    """Compare two sections' letter-grade mixes (count vectors aligned to the same letters)."""
    counts_a, counts_b = list(counts_a), list(counts_b)
    n_a, n_b = sum(counts_a), sum(counts_b)
    if n_a < 1 or n_b < 1:
        return LetterComparison(n_a, n_b, None, float("nan"))
    table = [counts_a, counts_b]
    return LetterComparison(
        n_a=n_a,
        n_b=n_b,
        chi2=chi2_contingency(table, n_resamples=n_resamples, rng=_rng(rng)),
        cramers_v=cramers_v(table),
    )
