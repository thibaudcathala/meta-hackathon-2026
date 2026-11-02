import unittest

from footfall.compare import segments

FUNNEL = ["product page", "cart", "checkout", "order confirmed"]


class SegmentsTest(unittest.TestCase):
    def test_turns_between_stages_add_up_to_the_session(self):
        result = {"verified": True, "turns": 22,
                  "funnel": {"product page": 2, "cart": 7, "checkout": 9, "order confirmed": 20}}
        spans = segments(FUNNEL, result)
        self.assertEqual(spans, {"start": 2, "product page": 5, "cart": 2, "checkout": 11,
                                 "order confirmed": 2})
        self.assertEqual(sum(spans.values()), 22)

    def test_someone_stuck_spends_the_rest_where_they_stopped(self):
        result = {"verified": False, "turns": 60, "funnel": {"product page": 4}}
        self.assertEqual(segments(FUNNEL, result), {"start": 4, "product page": 56})


if __name__ == "__main__":
    unittest.main()
