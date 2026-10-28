"""Runs *inside* Blender (blender --python apprentice/blender_probe.py).

A read-only observer for the verifier: a localhost socket, polled from a Blender timer, that
returns the scene state as JSON. The agent never talks to it; it only sees pixels and acts
through the GUI. The harness also uses it to checkpoint and rewind the file between attempts.

Protocol: one JSON object per line in, one JSON object per line out.
  {"cmd": "ping"} | {"cmd": "scene"} | {"cmd": "save", "path": ...} | {"cmd": "open", "path": ...}
"""

import json
import os
import socket

import bpy

PORT = int(os.environ.get("APPRENTICE_PROBE_PORT", "47615"))
SIMPLE = {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}


def clean(value):
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, (bool, int, str)) or value is None:
        return value
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    try:
        return [clean(v) for v in value]
    except TypeError:
        return str(value)


def rna_props(struct, skip=("rna_type", "name")):
    out = {}
    for prop in struct.bl_rna.properties:
        if prop.type in SIMPLE and prop.identifier not in skip:
            try:
                out[prop.identifier] = clean(getattr(struct, prop.identifier))
            except Exception:
                pass
    return out


def socket_value(node, name):
    sock = node.inputs.get(name)
    if sock is None or not hasattr(sock, "default_value"):
        return None
    return clean(sock.default_value)


def material_state(mat):
    state = {"name": mat.name, "diffuse_color": clean(mat.diffuse_color),
             "metallic": clean(mat.metallic), "roughness": clean(mat.roughness)}
    if mat.node_tree:
        state["nodes"] = sorted(n.bl_idname for n in mat.node_tree.nodes)
        for node in mat.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                for key, sock in (("base_color", "Base Color"), ("metallic", "Metallic"),
                                  ("roughness", "Roughness"), ("emission_color", "Emission Color"),
                                  ("emission_strength", "Emission Strength"),
                                  ("transmission", "Transmission Weight"), ("alpha", "Alpha")):
                    state[key] = socket_value(node, sock)
                break
    return state


def object_state(ob):
    state = {"name": ob.name, "type": ob.type, "location": clean(ob.location),
             "rotation_euler": clean(ob.rotation_euler), "scale": clean(ob.scale),
             "dimensions": clean(ob.dimensions), "parent": ob.parent.name if ob.parent else None,
             "selected": ob.select_get(), "visible": ob.visible_get(),
             "modifiers": [{"name": m.name, "type": m.type, **rna_props(m)} for m in ob.modifiers],
             "materials": [material_state(s.material) for s in ob.material_slots if s.material]}
    if ob.type == "MESH":
        if ob.mode == "EDIT":
            ob.update_from_editmode()  # edit-mode changes only reach ob.data on sync
        mesh = ob.data
        faces = len(mesh.polygons)
        state["mesh"] = {"vertices": len(mesh.vertices), "edges": len(mesh.edges), "faces": faces,
                         "smooth_fraction": clean(sum(p.use_smooth for p in mesh.polygons) / faces)
                         if faces else 0.0}
    elif ob.type == "LIGHT":
        state["light"] = {"type": ob.data.type, "energy": clean(ob.data.energy),
                          "color": clean(ob.data.color)}
    elif ob.type == "CAMERA":
        state["camera"] = {"lens": clean(ob.data.lens), "type": ob.data.type}
    return state


def scene_state():
    ctx = bpy.context
    scene = ctx.scene
    world = scene.world
    bg = None
    if world and world.node_tree:
        node = world.node_tree.nodes.get("Background")
        bg = socket_value(node, "Color") if node else None
    active = ctx.view_layer.objects.active
    return {"file": bpy.data.filepath, "mode": ctx.mode, "active": active.name if active else None,
            "render_engine": scene.render.engine, "frame": scene.frame_current,
            "camera": scene.camera.name if scene.camera else None, "world_color": bg,
            "objects": [object_state(ob) for ob in scene.objects]}


def handle(req):
    cmd = req.get("cmd")
    if cmd == "ping":
        return {"ok": True, "file": bpy.data.filepath}
    if cmd == "scene":
        return {"ok": True, "scene": scene_state()}
    if cmd == "save":
        bpy.ops.wm.save_as_mainfile(filepath=req["path"], copy=True)
        return {"ok": True}
    if cmd == "open":
        # Loading replaces the whole UI; do it after the reply has been sent.
        path = req["path"]
        bpy.app.timers.register(lambda: bpy.ops.wm.open_mainfile(filepath=path) and None,
                                first_interval=0.05)
        return {"ok": True}
    return {"ok": False, "error": f"unknown cmd {cmd!r}"}


server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("127.0.0.1", PORT))
server.listen(4)
server.setblocking(False)


def poll():
    try:
        conn, _ = server.accept()
    except BlockingIOError:
        return 0.05
    with conn:
        conn.settimeout(2.0)
        data = b""
        while not data.endswith(b"\n"):
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
        try:
            reply = handle(json.loads(data or b"{}"))
        except Exception as exc:
            reply = {"ok": False, "error": repr(exc)}
        try:
            conn.sendall(json.dumps(reply).encode() + b"\n")
        except OSError:
            pass
    return 0.01


bpy.app.timers.register(poll, persistent=True)
print(f"apprentice probe listening on 127.0.0.1:{PORT}", flush=True)
