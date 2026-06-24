"""Unit tests for the pure-Python statistics in apps/homework/stats.py.

The *statistics* are checked against hand-computed / textbook values; permutation *p-values*
are checked for sane, seed-reproducible behaviour (≈1 for identical samples, small for clearly
separated ones, and always in (0, 1]).
"""

import math
import random

from django.test import SimpleTestCase

from apps.homework import stats


class StatisticTests(SimpleTestCase):
    def test_welch_t_matches_formula(self):
        a, b = [1, 2, 3, 4], [3, 4, 5, 6]
        # means 2.5 vs 4.5; sample var 5/3 each; denom = 2*(5/3)/4 = 5/6
        expected = (2.5 - 4.5) / math.sqrt(5 / 6)
        self.assertAlmostEqual(stats._welch_t(a, b), expected, places=10)

    def test_welch_t_zero_variance(self):
        self.assertEqual(stats._welch_t([5, 5, 5], [5, 5, 5]), 0.0)
        self.assertEqual(stats._welch_t([9, 9], [1, 1]), math.inf)

    def test_average_ranks_with_ties(self):
        # values 10,10,20,30 -> the two 10s share ranks (1,2)->1.5; then 3,4
        self.assertEqual(stats._average_ranks([10, 10, 20, 30]), [1.5, 1.5, 3.0, 4.0])

    def test_mann_whitney_u_known(self):
        # x clearly below y, no ties -> U_x = 0
        result = stats.mannwhitneyu([1, 2, 3], [4, 5, 6], n_resamples=200)
        self.assertEqual(result.statistic, 0.0)

    def test_chi2_statistic_known_table(self):
        # [[10,20],[20,10]]: all expected cells = 15, each deviation ±5 -> 4 * 25/15.
        self.assertAlmostEqual(
            stats.chi2_statistic([[10, 20], [20, 10]]), 100 / 15, places=6
        )

    def test_cohens_d_known(self):
        a, b = [2, 4, 6], [4, 6, 8]  # mean diff -2, pooled sd 2 -> d = -1
        self.assertAlmostEqual(stats.cohens_d(a, b), -1.0, places=10)

    def test_cliffs_delta_extremes(self):
        self.assertEqual(stats.cliffs_delta([1, 2, 3], [4, 5, 6]), -1.0)
        self.assertEqual(stats.cliffs_delta([4, 5, 6], [1, 2, 3]), 1.0)
        self.assertEqual(stats.cliffs_delta([1, 2, 3], [1, 2, 3]), 0.0)

    def test_cramers_v_range(self):
        self.assertAlmostEqual(stats.cramers_v([[5, 0], [0, 5]]), 1.0, places=10)
        self.assertAlmostEqual(stats.cramers_v([[5, 5], [5, 5]]), 0.0, places=10)

    def test_benjamini_hochberg_matches_reference(self):
        # Classic worked example.
        ps = [0.01, 0.02, 0.03, 0.04, 0.05]
        adjusted = stats.false_discovery_control(ps)
        self.assertEqual(
            [round(p, 3) for p in adjusted], [0.05, 0.05, 0.05, 0.05, 0.05]
        )

    def test_benjamini_hochberg_is_monotone_and_bounded(self):
        ps = [0.9, 0.001, 0.5, 0.02]
        adjusted = stats.false_discovery_control(ps)
        self.assertTrue(all(0 <= p <= 1 for p in adjusted))
        self.assertTrue(all(adj >= raw for adj, raw in zip(adjusted, ps)))


class PermutationPValueTests(SimpleTestCase):
    def _rng(self):
        return random.Random(42)

    def test_identical_samples_pvalue_near_one(self):
        a = [70, 80, 90, 60, 75]
        result = stats.ttest_ind(a, list(a), n_resamples=2000, rng=self._rng())
        self.assertGreater(result.pvalue, 0.5)

    def test_separated_samples_pvalue_small(self):
        a = [95, 92, 98, 90, 99]
        b = [40, 45, 50, 38, 42]
        result = stats.ttest_ind(a, b, n_resamples=2000, rng=self._rng())
        self.assertLess(result.pvalue, 0.05)

    def test_pvalue_in_unit_interval(self):
        for fn_args in (
            (stats.ttest_ind, ([1, 2, 3, 4], [2, 3, 4, 5])),
            (stats.mannwhitneyu, ([1, 2, 3, 4], [2, 3, 4, 5])),
        ):
            fn, (a, b) = fn_args
            p = fn(a, b, n_resamples=500, rng=self._rng()).pvalue
            self.assertTrue(0 < p <= 1)

    def test_reproducible_with_seed(self):
        a, b = [10, 20, 30, 40], [15, 25, 35, 45]
        p1 = stats.ttest_ind(a, b, n_resamples=1000, rng=random.Random(7)).pvalue
        p2 = stats.ttest_ind(a, b, n_resamples=1000, rng=random.Random(7)).pvalue
        self.assertEqual(p1, p2)

    def test_chi2_contingency_detects_difference(self):
        same = stats.chi2_contingency(
            [[10, 10], [10, 10]], n_resamples=1000, rng=self._rng()
        )
        diff = stats.chi2_contingency(
            [[18, 2], [2, 18]], n_resamples=1000, rng=self._rng()
        )
        self.assertGreater(same.pvalue, 0.3)
        self.assertLess(diff.pvalue, 0.01)


class CompareHelperTests(SimpleTestCase):
    def test_compare_scores_insufficient_data(self):
        result = stats.compare_scores([90], [80, 70])
        self.assertFalse(result.comparable)
        self.assertIsNone(result.welch)

    def test_compare_scores_full(self):
        result = stats.compare_scores(
            [95, 92, 98, 90], [55, 60, 58, 62], n_resamples=1000
        )
        self.assertTrue(result.comparable)
        self.assertLess(result.welch.pvalue, 0.05)
        self.assertGreater(result.cohens_d, 0)

    def test_compare_letters(self):
        result = stats.compare_letters(
            [8, 1, 1, 0, 0], [0, 1, 1, 1, 7], n_resamples=1000
        )
        self.assertTrue(result.comparable)
        self.assertLess(result.chi2.pvalue, 0.05)


class EdgeCaseTests(SimpleTestCase):
    """The defensive guards: degenerate inputs return nan/0.0, unsupported options raise."""

    def test_sample_var_of_singleton_is_zero(self):
        self.assertEqual(stats._sample_var([5.0], 5.0), 0.0)

    def test_welch_t_constant_groups(self):
        self.assertEqual(
            stats._welch_t([1.0, 1.0], [1.0, 1.0]), 0.0
        )  # equal & constant
        self.assertEqual(stats._welch_t([2.0, 2.0], [1.0, 1.0]), math.inf)
        self.assertEqual(stats._welch_t([1.0, 1.0], [2.0, 2.0]), -math.inf)

    def test_ttest_equal_var_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            stats.ttest_ind([1, 2, 3], [4, 5, 6], equal_var=True)

    def test_chi2_statistic_of_empty_table_is_zero(self):
        self.assertEqual(stats.chi2_statistic([[0, 0], [0, 0]]), 0.0)

    def test_chi2_contingency_requires_two_rows(self):
        with self.assertRaises(NotImplementedError):
            stats.chi2_contingency([[1, 2], [3, 4], [5, 6]])

    def test_false_discovery_control_only_bh(self):
        with self.assertRaises(NotImplementedError):
            stats.false_discovery_control([0.1, 0.2], method="by")

    def test_cohens_d_degenerate(self):
        self.assertTrue(math.isnan(stats.cohens_d([1.0], [2.0, 3.0])))  # group < 2
        self.assertEqual(stats.cohens_d([1.0, 1.0], [2.0, 2.0]), 0.0)  # zero pooled var

    def test_cramers_v_degenerate(self):
        self.assertTrue(math.isnan(stats.cramers_v([[0, 0], [0, 0]])))  # n == 0
        self.assertEqual(stats.cramers_v([[1], [2]]), 0.0)  # single column -> k < 2

    def test_compare_letters_with_an_empty_section(self):
        result = stats.compare_letters([0, 0, 0, 0, 0], [1, 2, 0, 0, 0])
        self.assertEqual(result.n_a, 0)
        self.assertIsNone(result.chi2)
        self.assertFalse(result.comparable)

    def test_compare_scores_with_too_few_observations(self):
        result = stats.compare_scores([5.0], [1.0, 2.0, 3.0])
        self.assertIsNone(result.welch)
        self.assertFalse(result.comparable)
        self.assertEqual(result.mean_a, 5.0)
