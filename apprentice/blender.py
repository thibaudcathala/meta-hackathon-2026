"""Blender as the target app: launch it on the virtual desktop and talk to its probe."""

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

PROBE = Path(__file__).resolve().parent / "blender_probe.py"


def blender_binary(name="blender"):
    """The real executable: snap's launcher cannot run inside bwrap, its payload can."""
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"{name} not found")
    if path.startswith("/snap/bin/"):
        inner = Path("/snap") / Path(path).name / "current" / Path(path).name
        if inner.exists():
            return str(inner)
    return path


def sandboxed(argv, work):
    """Wrap argv in bubblewrap: read-only system, empty home, only the run dir writable.

    The agent drives a full app (file browser included) with model-chosen input; this keeps
    the user's files out of its reach whatever it clicks.
    """
    home = os.path.expanduser("~")
    return ["bwrap", "--ro-bind", "/", "/", "--dev-bind", "/dev", "/dev", "--proc", "/proc",
            "--tmpfs", "/tmp", "--bind", "/tmp/.X11-unix", "/tmp/.X11-unix",
            "--tmpfs", home, "--bind", str(work), str(work),
            "--ro-bind", str(PROBE.parent), str(PROBE.parent),
            "--unshare-ipc", "--die-with-parent", "--new-session", "--setenv", "HOME", str(work),
            "--"] + argv


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Blender:
    def __init__(self, desktop, work_dir, binary="blender", sandbox=None):
        self.desktop = desktop
        self.work = Path(work_dir).resolve()
        self.binary = blender_binary(binary)
        self.sandbox = shutil.which("bwrap") is not None if sandbox is None else sandbox
        self.port = free_port()
        # Private config dirs: the run must never read or write the user's own Blender prefs.
        self.dirs = {"BLENDER_USER_CONFIG": self.work / "config",
                     "BLENDER_USER_SCRIPTS": self.work / "scripts",
                     "BLENDER_USER_DATAFILES": self.work / "datafiles"}
        self.proc = None

    def _env(self):
        for d in self.dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        return {k: str(v) for k, v in self.dirs.items()} | {"APPRENTICE_PROBE_PORT": str(self.port)}

    def start_file(self):
        """Factory default scene and prefs (without the splash) in the private config dir."""
        path = self.work / "start.blend"
        if not path.exists():
            script = ("import bpy; bpy.context.preferences.view.show_splash = False; "
                      "bpy.ops.wm.save_userpref(); "
                      f"bpy.ops.wm.save_as_mainfile(filepath={str(path)!r})")
            subprocess.run([self.binary, "-b", "--factory-startup", "--python-expr", script],
                           env=self.desktop.env(**self._env()), check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return path

    def launch(self, blend=None, timeout=90):
        w, h = self.desktop.size
        blend = blend or self.start_file()
        argv = [self.binary, "--window-geometry", "0", "0", str(w), str(h),
                str(blend), "--python", str(PROBE)]
        if self.sandbox:
            argv = sandboxed(argv, self.work)
        self.proc = self.desktop.launch(argv, "blender.log", **self._env())
        self.wait_ready(timeout)
        time.sleep(2.0)  # first frames: let the UI finish drawing before anyone looks at it
        return self

    def request(self, cmd, timeout=10.0, **args):
        with socket.create_connection(("127.0.0.1", self.port), timeout=timeout) as s:
            s.sendall(json.dumps({"cmd": cmd, **args}).encode() + b"\n")
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(1 << 20)
                if not chunk:
                    break
                data += chunk
        reply = json.loads(data)
        if not reply.get("ok"):
            raise RuntimeError(f"probe {cmd}: {reply.get('error')}")
        return reply

    def wait_ready(self, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc and self.proc.poll() is not None:
                raise RuntimeError(f"blender exited with {self.proc.returncode}, see blender.log")
            try:
                return self.request("ping", timeout=2)
            except (OSError, ValueError):
                time.sleep(0.3)
        raise TimeoutError("blender probe did not answer")

    def scene(self):
        return self.request("scene")["scene"]

    def checkpoint(self, path):
        self.request("save", path=str(Path(path).resolve()))

    def rewind(self, path, settle=1.5):
        self.request("open", path=str(Path(path).resolve()))
        time.sleep(0.5)
        self.wait_ready()
        time.sleep(settle)
