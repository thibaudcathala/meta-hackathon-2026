import unittest

from footfall.wall import H, W, layout


class LayoutTest(unittest.TestCase):
    def test_mixed_devices_fit_the_frame_and_keep_their_shape(self):
        aspects = [1.6, 1.6, 0.75] + [0.47] * 5
        boxes = layout(aspects)
        self.assertEqual(len(boxes), len(aspects))
        for (x, y, w, h), a in zip(boxes, aspects):
            self.assertGreaterEqual(x, 0)
            self.assertLessEqual(x + w, W)
            self.assertLessEqual(y + h + 48, H)
            self.assertAlmostEqual(w / h, a, delta=0.02)

    def test_tiles_do_not_overlap(self):
        boxes = layout([1.6] * 5 + [0.47] * 4)
        for i, (x, y, w, h) in enumerate(boxes):
            for (x2, y2, w2, h2) in boxes[i + 1:]:
                self.assertTrue(x + w <= x2 or x2 + w2 <= x or y + h + 48 <= y2 or y2 + h2 + 48 <= y)


if __name__ == "__main__":
    unittest.main()
