"""Checks a step's expected outcome against the scene read by the probe (ground truth).

A step carries a list of checks, small JSON objects the tutorial reader writes:

  {"op": "exists",   "type": "MESH", "name": "Sphere*"}
  {"op": "absent",   "name": "Cube"}
  {"op": "count",    "type": "MESH", "eq": 1}
  {"op": "modifier", "object": "@active", "type": "SUBSURF", "props": {"levels": 2}}
  {"op": "material", "object": "@active", "base_color": [0.8, 0.1, 0.1], "tol": 0.15}
  {"op": "prop",     "object": "@active", "path": "dimensions.2", "approx": 1.2, "tol": 0.05}
  {"op": "prop",     "path": "mode", "eq": "OBJECT"}
  {"op": "distance", "object": "Sphere*", "to": "Cube", "gte": 2.5}

`object` is a name glob, "@active", or "@new" (created during this step). `prop` compares with
eq, approx (+tol), gte or lte, and so does `distance` (between object origins; `to` is an object
or an [x, y, z] point); a path is dot-separated, and "modifiers[BEVEL]" picks a list
entry by type or name. Object paths: location, rotation_euler, scale, dimensions (x, y, z
lists), mesh.vertices, mesh.faces, mesh.smooth_fraction, modifiers[TYPE].<Python API property>,
materials.0.<base_color|metallic|roughness>, light.energy, camera.lens. Scene paths: mode,
active, render_engine, frame, camera, world_color.
"""

import fnmatch
import re


def _match(ob, check):
    return (fnmatch.fnmatchcase(ob["name"], check.get("name", "*"))
            and check.get("type", ob["type"]) == ob["type"])


def find_objects(scene, ref, before=None):
    objects = scene["objects"]
    if ref in (None, "@active"):
        return [o for o in objects if o["name"] == scene["active"]]
    if ref == "@new":
        old = {o["name"] for o in (before or {"objects": []})["objects"]}
        return [o for o in objects if o["name"] not in old]
    return [o for o in objects if fnmatch.fnmatchcase(o["name"], ref)]


def walk(value, path):
    for part in filter(None, path.split(".")):
        m = re.fullmatch(r"(\w+)\[([^\]]+)\]", part)
        if m:
            items = value[m.group(1)]
            key = m.group(2)
            hits = [i for i in items if key in (i.get("type"), i.get("name"))]
            if not hits:
                raise KeyError(f"no {m.group(1)} entry {key!r}")
            value = hits[0]
        elif isinstance(value, list):
            value = value[int(part)]
        else:
            value = value[part]
    return value


def close(actual, expected, tol):
    if isinstance(expected, (list, tuple)):
        # Colours may come with or without alpha: compare the components both sides have.
        return all(close(a, e, tol) for a, e in zip(actual, expected))
    return abs(float(actual) - float(expected)) <= tol


def compare(actual, check):
    tol = check.get("tol", 1e-3)
    if "eq" in check and actual != check["eq"]:
        return f"is {actual!r}, expected {check['eq']!r}"
    if "approx" in check and not close(actual, check["approx"], tol):
        return f"is {actual!r}, expected {check['approx']!r} (±{tol})"
    if "gte" in check and not actual >= check["gte"]:
        return f"is {actual!r}, expected at least {check['gte']!r}"
    if "lte" in check and not actual <= check["lte"]:
        return f"is {actual!r}, expected at most {check['lte']!r}"
    return None


def run_check(check, scene, before=None):
    """Return None when the check passes, else a sentence saying what is wrong."""
    op = check["op"]
    if op in ("exists", "absent", "count"):
        hits = [o for o in scene["objects"] if _match(o, check)]
        what = f"{check.get('type', 'object').lower()} named {check.get('name', '*')!r}"
        if op == "exists":
            return None if hits else f"no {what} in the scene"
        if op == "absent":
            return f"{what} should be gone but {[o['name'] for o in hits]} remain" if hits else None
        problem = compare(len(hits), check)
        return f"count of {what} {problem}" if problem else None

    if op == "prop" and "object" not in check:
        try:
            problem = compare(walk(scene, check["path"]), check)
        except (KeyError, IndexError) as exc:
            problem = f"is missing ({exc})"
        return f"scene {check['path']} {problem}" if problem else None

    targets = find_objects(scene, check.get("object"), before)
    if not targets:
        return f"no object matches {check.get('object', '@active')!r}"
    reasons = []
    for ob in targets:
        reason = _object_check(op, check, ob, scene)
        if reason is None:
            return None
        reasons.append(f"{ob['name']}: {reason}")
    return "; ".join(reasons)


def _object_check(op, check, ob, scene):
    if op == "modifier":
        mods = [m for m in ob["modifiers"] if m["type"] == check["type"]]
        if not mods:
            have = [m["type"] for m in ob["modifiers"]] or "none"
            return f"has no {check['type']} modifier (modifiers: {have})"
        for key, want in check.get("props", {}).items():
            problem = compare(mods[0].get(key), {"eq": want} if not isinstance(want, float)
                              else {"approx": want, "tol": check.get("tol", 0.02)})
            if problem:
                return f"{check['type']}.{key} {problem}"
        return None
    if op == "material":
        if not ob["materials"]:
            return "has no material"
        mat = ob["materials"][0]
        for key in ("base_color", "metallic", "roughness", "emission_strength", "transmission"):
            if key in check:
                problem = compare(mat.get(key), {"approx": check[key], "tol": check.get("tol", 0.1)})
                if problem:
                    return f"material {mat['name']} {key} {problem}"
        return None
    if op == "distance":
        to = check["to"]
        if isinstance(to, str):
            hits = [o for o in scene["objects"] if fnmatch.fnmatchcase(o["name"], to)]
            if not hits:
                return f"no object {to!r} to measure from"
            to = hits[0]["location"]
        gap = round(sum((a - b) ** 2 for a, b in zip(ob["location"], to)) ** 0.5, 4)
        problem = compare(gap, check)
        return f"distance to {check['to']} {problem}" if problem else None
    if op == "prop":
        try:
            problem = compare(walk(ob, check["path"]), check)
        except (KeyError, IndexError) as exc:
            problem = f"is missing ({exc})"
        return f"{check['path']} {problem}" if problem else None
    return f"unknown check op {op!r}"


def verify(checks, scene, before=None):
    results = [{"check": c, "error": run_check(c, scene, before)} for c in checks]
    return all(r["error"] is None for r in results), results
