import unittest

from driver.comparison import RULES, compare, decision, grade, summarize


def photo(label=None):
    if label is None:
        return {"score": None, "reason": "보류"}
    values = [0.0] * 10
    values[label] = 1.0
    return decision(values, RULES)


class ComparisonTests(unittest.TestCase):
    def test_scores_and_boundaries(self):
        self.assertEqual([grade(n, RULES) for n in (59.99, 60, 84.99, 85)],
                         ["위험 운전", "일반 운전", "일반 운전", "안전 운전"])
        self.assertEqual(photo(1)["score"], 0)
        self.assertEqual(photo(6)["grade"], "위험 운전")
        self.assertEqual(decision([.7, .3] + [0] * 8, RULES)["score"], 100)
        self.assertIsNone(decision([.699, .301] + [0] * 8, RULES)["score"])

    def test_order_and_holds(self):
        asc = summarize([photo(1), photo(), photo(0), photo(0)], RULES)
        desc = summarize([photo(0), photo(0), photo(1)], RULES)
        self.assertAlmostEqual(asc["ema"], 77.6, places=1)
        self.assertAlmostEqual(desc["ema"], 54.3, places=1)
        self.assertIsNone(asc["trace"][1])
        self.assertIsNone(asc["weights"][1])
        self.assertAlmostEqual(sum(x for x in asc["weights"] if x is not None), 1)
        self.assertEqual(asc["actions"][0], [3, 4])
        self.assertIsNone(summarize([photo()], RULES)["ema"])
        self.assertEqual(summarize([photo(1)], RULES)["ema"], 0)
        self.assertEqual(summarize([photo(0)] * 24, RULES)["ema"], 100)

    def test_common_observations(self):
        data = [{"results": {"A": photo(a), "B": photo(b)}} for a,b in [(0,0),(1,None),(1,1),(None,0)]]
        result = compare(data, RULES)
        self.assertEqual(result["common"]["count"], 2)
        self.assertEqual(result["common"]["delta"], 0)
        self.assertNotEqual(result["models"]["A"]["ema"], result["models"]["B"]["ema"])
        self.assertIsNone(compare(data[:2], RULES)["common"]["delta"])
