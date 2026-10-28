import unittest

from apprentice.verify import verify

SCENE = {"mode": "OBJECT", "active": "Sphere", "objects": [
    {"name": "Sphere", "type": "MESH", "location": [0, 0, 0], "dimensions": [2.0, 2.0, 1.2],
     "rotation_euler": [0.5236, 0.0, 0.0], "mesh": {"smooth_fraction": 1.0},
     "modifiers": [{"name": "Subdivision", "type": "SUBSURF", "levels": 2}],
     "materials": [{"name": "Red", "base_color": [0.8, 0.05, 0.05, 1.0], "metallic": 0.0}]},
    {"name": "Light", "type": "LIGHT", "location": [0, 0, 0], "dimensions": [0, 0, 0], "modifiers": [], "materials": []},
]}
BEFORE = {"objects": [{"name": "Light"}]}


def errors(*checks):
    return [r["error"] for r in verify(list(checks), SCENE, BEFORE)[1]]


class VerifyTest(unittest.TestCase):
    def test_passing_checks(self):
        ok, _ = verify([
            {"op": "exists", "type": "MESH", "name": "Sph*"},
            {"op": "absent", "name": "Cube"},
            {"op": "count", "type": "MESH", "eq": 1},
            {"op": "modifier", "object": "@active", "type": "SUBSURF", "props": {"levels": 2}},
            {"op": "material", "object": "@new", "base_color": [0.8, 0.0, 0.0], "tol": 0.1},
            {"op": "prop", "object": "Sphere", "path": "dimensions.2", "approx": 1.2, "tol": 0.01},
            {"op": "prop", "object": "Sphere", "path": "modifiers[SUBSURF].levels", "gte": 1},
            {"op": "prop", "path": "mode", "eq": "OBJECT"},
            {"op": "distance", "object": "Sphere", "to": [0, 0, 3], "approx": 3.0},
            {"op": "distance", "object": "Sphere", "to": "Light", "lte": 0.1},
        ], SCENE, BEFORE)
        self.assertTrue(ok)

    def test_failures_explain_themselves(self):
        self.assertIn("expected 0.6", errors(
            {"op": "prop", "object": "@active", "path": "dimensions.2", "approx": 0.6})[0])
        self.assertIn("no BEVEL modifier", errors(
            {"op": "modifier", "object": "Sphere", "type": "BEVEL"})[0])
        self.assertIn("expected at least 5", errors(
            {"op": "distance", "object": "Sphere", "to": [3, 4, 0], "gte": 5.5})[0])
        self.assertIn("remain", errors({"op": "absent", "name": "Light"})[0])
        self.assertIn("no object matches", errors(
            {"op": "prop", "object": "Cube", "path": "scale.0", "eq": 1})[0])
        self.assertIn("missing", errors(
            {"op": "prop", "object": "Sphere", "path": "modifiers[BEVEL].width", "eq": 1})[0])

    def test_any_matching_object_passes(self):
        self.assertEqual(errors({"op": "prop", "object": "*", "path": "dimensions.0",
                                 "approx": 2.0}), [None])


if __name__ == "__main__":
    unittest.main()
