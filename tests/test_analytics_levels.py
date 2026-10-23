"""Unit tests for quantcore.analytics.levels — confluence clustering and scoring."""

import unittest

from quantcore.analytics import levels


def _lvl(level, method="sma_50"):
    return levels.make_level(method, level, "test")


class MakeLevelTests(unittest.TestCase):
    def test_rounds_and_weights(self):
        lvl = levels.make_level("gamma_wall", "95.123", "wall")
        self.assertEqual(lvl, {"level": 95.12, "method": "gamma_wall",
                               "weight": 1.0, "detail": "wall"})

    def test_rejects_missing_non_numeric_non_positive_and_nan(self):
        for bad in (None, "abc", 0, -5, float("nan")):
            with self.subTest(bad=bad):
                self.assertIsNone(levels.make_level("sma_50", bad, "x"))

    def test_unknown_method_raises(self):
        with self.assertRaises(KeyError):
            levels.make_level("astrology", 95.0, "x")


class ClusterLevelsTests(unittest.TestCase):
    def test_levels_within_tolerance_merge(self):
        clusters = levels.cluster_levels([_lvl(95.0), _lvl(95.5, "rolling_vwap")], 100.0, 1.0)
        self.assertEqual(len(clusters), 1)

    def test_levels_beyond_tolerance_split(self):
        clusters = levels.cluster_levels([_lvl(95.0), _lvl(95.5, "rolling_vwap")], 100.0, 0.4)
        self.assertEqual(len(clusters), 2)

    def test_window_is_25pct_inclusive(self):
        kept = levels.cluster_levels([_lvl(75.0), _lvl(125.0)], 100.0, 1.0)
        dropped = levels.cluster_levels([_lvl(74.99), _lvl(125.01)], 100.0, 1.0)
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, [])

    def test_input_order_does_not_matter(self):
        a = levels.cluster_levels([_lvl(97.0), _lvl(90.0), _lvl(96.5, "sma_100")], 100.0, 1.0)
        b = levels.cluster_levels([_lvl(90.0), _lvl(96.5, "sma_100"), _lvl(97.0)], 100.0, 1.0)
        self.assertEqual(a, b)


class ScoreZoneTests(unittest.TestCase):
    def test_distinct_methods_sum_and_repeats_add_point_two(self):
        members = [_lvl(95.0, "rolling_vwap"), _lvl(95.0, "sma_50"), _lvl(95.2, "sma_50")]
        zone = levels.score_zone(members, 100.0)
        self.assertEqual(zone["method_count"], 2)
        self.assertAlmostEqual(zone["score"], 0.7 + 0.6 + 0.2)
        self.assertEqual((zone["zone_low"], zone["zone_high"]), (95.0, 95.2))
        self.assertLess(zone["distance_pct"], 0)

    def test_contributors_sorted_by_weight(self):
        zone = levels.score_zone([_lvl(95.0, "fibonacci"), _lvl(95.0, "gamma_wall")], 100.0)
        self.assertEqual([c["method"] for c in zone["contributors"]], ["gamma_wall", "fibonacci"])


class RankZonesTests(unittest.TestCase):
    def _zone(self, center, score):
        return {"center": center, "score": score}

    def test_splits_at_price_and_caps_each_side(self):
        zones = [self._zone(c, s) for c, s in
                 [(90, 1.0), (95, 2.0), (99, 0.5), (100, 3.0), (105, 1.5), (110, 0.4)]]
        support, resistance = levels.rank_zones(zones, 100.0, 2)
        self.assertEqual([z["center"] for z in support], [100, 95])   # at price counts as support
        self.assertEqual([z["center"] for z in resistance], [105, 110])


class InterpretationTests(unittest.TestCase):
    def test_no_support_and_failed_sources(self):
        text = levels.confluence_interpretation(None, [], ["gamma_wall"])
        self.assertIn("No support zones", text)
        self.assertIn("Sources unavailable: gamma_wall.", text)


class MarkStopZoneTests(unittest.TestCase):
    def _z(self, center, score):
        return {"zone_low": center - 0.5, "zone_high": center + 0.5, "center": center,
                "score": score}

    def test_marks_the_stop_zone_in_place(self):
        top = [self._z(95.0, 3.0), self._z(98.0, 1.0)]
        stop = {"zone_low": 97.5, "zone_high": 98.5, "center": 98.0, "stop": 96.72}
        marked = levels.mark_stop_zone(top, top, stop)
        self.assertEqual([z.get("is_stop_zone") for z in marked], [None, True])
        self.assertNotIn("outside_top", marked[1])
        self.assertNotIn("is_stop_zone", top[1])   # inputs untouched

    def test_appends_a_zone_the_ranking_cut(self):
        every = [self._z(95.0, 3.0), self._z(98.0, 1.0)]
        stop = {"zone_low": 97.5, "zone_high": 98.5, "center": 98.0}
        marked = levels.mark_stop_zone(every[:1], every, stop)
        self.assertEqual([z["center"] for z in marked], [95.0, 98.0])
        self.assertEqual((marked[1]["is_stop_zone"], marked[1]["outside_top"]), (True, True))

    def test_no_stop_leaves_the_zones_alone(self):
        top = [self._z(95.0, 3.0)]
        self.assertEqual(levels.mark_stop_zone(top, top, None), top)


if __name__ == "__main__":
    unittest.main()
