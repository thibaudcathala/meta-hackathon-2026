"""An isolated X display the agent can see and drive: Xvfb + XTEST input + XGetImage capture.

Nothing here touches the user's real session: apps are launched on the private display with
Wayland disabled, so they cannot open on the real desktop.
"""

import io
import os
import subprocess
import time
from pathlib import Path

from PIL import Image, ImageDraw
from Xlib import X, XK, display
from Xlib.ext import xtest

KEY_NAMES = {
    "ctrl": "Control_L", "control": "Control_L", "shift": "Shift_L", "alt": "Alt_L",
    "option": "Alt_L", "cmd": "Super_L", "command": "Super_L", "meta": "Super_L",
    "super": "Super_L", "win": "Super_L", "enter": "Return", "return": "Return",
    "esc": "Escape", "escape": "Escape", "tab": "Tab", "space": "space",
    "backspace": "BackSpace", "delete": "Delete", "del": "Delete", "insert": "Insert",
    "home": "Home", "end": "End", "pageup": "Prior", "page_up": "Prior", "pagedown": "Next",
    "page_down": "Next", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "arrowup": "Up", "arrowdown": "Down", "arrowleft": "Left", "arrowright": "Right",
    "minus": "minus", "plus": "plus", "period": "period", "comma": "comma", "slash": "slash",
}
CHAR_KEYSYMS = {"\n": "Return", "\t": "Tab", " ": "space"}
BUTTONS = {"left": 1, "wheel": 2, "middle": 2, "right": 3, "back": 8, "forward": 9}

# Small arrow drawn on captures so screenshots and recordings show where the agent points.
CURSOR = [(0, 0), (0, 17), (4, 13), (7, 20), (10, 19), (7, 12), (12, 12)]


def keysym_for(name):
    if name in CHAR_KEYSYMS:
        name = CHAR_KEYSYMS[name]
    low = name.lower()
    if low in KEY_NAMES:
        name = KEY_NAMES[low]
    elif low.startswith("numpad") or low.startswith("kp_"):
        name = "KP_" + low.removeprefix("numpad").removeprefix("kp_").capitalize()
    elif len(low) > 1 and low[0] == "f" and low[1:].isdigit():
        name = low.upper()
    sym = XK.string_to_keysym(name)
    if sym == 0 and len(name) == 1:
        sym = ord(name) if ord(name) < 256 else 0x01000000 | ord(name)  # X11 Unicode keysyms
    if sym == 0:
        raise ValueError(f"unknown key {name!r}")
    return sym


class VirtualDesktop:
    def __init__(self, width=1280, height=800, log_dir="runs"):
        self.size = (width, height)
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.procs = []
        self.pointer = (width // 2, height // 2)
        self.num = None
        self.d = None

    @property
    def name(self):
        return f":{self.num}"

    def start(self):
        # Xvfb picks a free display number itself and reports it on -displayfd: choosing one
        # from the lock files raced when several personas started at once (two shared :90).
        w, h = self.size
        log = open(self.log_dir / "xvfb.log", "wb")
        read_fd, write_fd = os.pipe()
        self.procs.append(subprocess.Popen(
            ["Xvfb", "-displayfd", str(write_fd), "-screen", "0", f"{w}x{h}x24",
             "-nolisten", "tcp"], stdout=log, stderr=subprocess.STDOUT, pass_fds=(write_fd,)))
        os.close(write_fd)
        with os.fdopen(read_fd) as pipe:
            line = pipe.readline().strip()
        if not line:
            raise RuntimeError(f"Xvfb did not start, see {self.log_dir / 'xvfb.log'}")
        self.num = int(line)
        self.d = display.Display(self.name)
        self.root = self.d.screen().root
        return self

    def env(self, **extra):
        env = {**os.environ, "DISPLAY": self.name, "WAYLAND_DISPLAY": "no-such-display",
               "XDG_SESSION_TYPE": "x11", **extra}
        env.pop("WAYLAND_SOCKET", None)
        return env

    def launch(self, argv, log_name, **env):
        log = open(self.log_dir / log_name, "wb")
        proc = subprocess.Popen(argv, env=self.env(**env), stdout=log, stderr=subprocess.STDOUT)
        self.procs.append(proc)
        return proc

    def record(self, path, fps=20):
        """Film the whole display (stop it with stop_recording) for the demo and the audit."""
        w, h = self.size
        self.recorder = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "x11grab", "-framerate", str(fps),
             "-video_size", f"{w}x{h}", "-i", self.name, "-c:v", "libx264", "-preset", "veryfast",
             "-pix_fmt", "yuv420p", str(path)], stdin=subprocess.PIPE)
        return self.recorder

    def stop_recording(self):
        rec = getattr(self, "recorder", None)
        if rec and rec.poll() is None:
            rec.communicate(b"q", timeout=30)
        self.recorder = None

    def stop(self):
        self.stop_recording()
        for p in reversed(self.procs):
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()
        self.procs.clear()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    # -- capture -------------------------------------------------------------------------

    def grab(self, cursor=True):
        w, h = self.size
        raw = self.root.get_image(0, 0, w, h, X.ZPixmap, 0xFFFFFFFF)
        img = Image.frombytes("RGB", (w, h), raw.data, "raw", "BGRX")
        if cursor:
            x, y = self.pointer
            ImageDraw.Draw(img).polygon([(x + dx, y + dy) for dx, dy in CURSOR],
                                        fill="white", outline="black")
        return img

    def settle(self, quiet=0.3, timeout=4.0, min_wait=0.25):
        """Wait until the screen stops changing (Blender can take a second to redraw, e.g. while
        it compiles shaders on first entering a mode); a capture taken too early makes the
        model think its action did nothing, and it repeats it."""
        time.sleep(min_wait)
        w, h = self.size
        last, still, start = None, time.time(), time.time()
        while time.time() - start < timeout:
            data = self.root.get_image(0, 0, w, h, X.ZPixmap, 0xFFFFFFFF).data
            if data != last:
                last, still = data, time.time()
            elif time.time() - still >= quiet:
                return True
            time.sleep(0.05)
        return False

    def screenshot(self):
        buf = io.BytesIO()
        self.grab().save(buf, "PNG", optimize=False, compress_level=3)
        return buf.getvalue()

    # -- input ---------------------------------------------------------------------------

    def _fake(self, *args, **kw):
        xtest.fake_input(self.d, *args, **kw)
        self.d.sync()

    def move(self, x, y):
        w, h = self.size
        x, y = max(0, min(int(x), w - 1)), max(0, min(int(y), h - 1))
        self._fake(X.MotionNotify, x=x, y=y)
        self.pointer = (x, y)

    def glide(self, x, y, steps=8, dt=0.01):
        x0, y0 = self.pointer
        for i in range(1, steps + 1):
            self.move(x0 + (x - x0) * i / steps, y0 + (y - y0) * i / steps)
            time.sleep(dt)

    def click(self, x, y, button="left", count=1):
        # Jump, do not glide: a straight path through an open menu hovers other entries and
        # Blender swaps the submenu under the cursor (seen on Add > Mesh > UV Sphere).
        self.move(x, y)
        time.sleep(0.12)
        code = BUTTONS[button]
        for _ in range(count):
            self._fake(X.ButtonPress, code)
            time.sleep(0.03)
            self._fake(X.ButtonRelease, code)
            time.sleep(0.06)

    def drag(self, path):
        self.glide(path[0]["x"], path[0]["y"])
        self._fake(X.ButtonPress, 1)
        for point in path[1:]:
            self.glide(point["x"], point["y"], steps=12, dt=0.015)
        time.sleep(0.05)
        self._fake(X.ButtonRelease, 1)

    def scroll(self, x, y, dx=0, dy=0):
        self.move(x, y)
        for delta, neg, pos in ((dy, 4, 5), (dx, 6, 7)):
            for _ in range(min(abs(int(delta)) // 100 or (1 if delta else 0), 20)):
                self._fake(X.ButtonPress, pos if delta > 0 else neg)
                self._fake(X.ButtonRelease, pos if delta > 0 else neg)
                time.sleep(0.03)

    def keypress(self, keys):
        codes = [self.d.keysym_to_keycode(keysym_for(k)) for k in keys]
        for c in codes:
            self._fake(X.KeyPress, c)
            time.sleep(0.01)
        for c in reversed(codes):
            self._fake(X.KeyRelease, c)
        time.sleep(0.05)

    def _borrow_keycode(self, sym):
        """Map a character missing from the US keymap (í, ø, ß...) onto a spare keycode."""
        first = self.d.display.info.min_keycode
        count = self.d.display.info.max_keycode - first + 1
        mapping = self.d.get_keyboard_mapping(first, count)
        spare = next((first + i for i in range(count - 1, -1, -1) if not any(mapping[i])), None)
        if spare is None:
            raise ValueError(f"cannot type keysym {sym:#x}: no spare keycode")
        self.d.change_keyboard_mapping(spare, [(sym, sym)])
        self.d.sync()
        time.sleep(0.05)
        return [(spare, 0)]

    def type(self, text):
        shift = self.d.keysym_to_keycode(XK.string_to_keysym("Shift_L"))
        for ch in text:
            sym = keysym_for(ch)
            codes = list(self.d.keysym_to_keycodes(sym)) or self._borrow_keycode(sym)
            code, index = codes[0]
            if index & 1:
                self._fake(X.KeyPress, shift)
            self._fake(X.KeyPress, code)
            self._fake(X.KeyRelease, code)
            if index & 1:
                self._fake(X.KeyRelease, shift)
            time.sleep(0.02)

    def execute(self, action):
        """Run one action of the Meta computer tool vocabulary."""
        kind = action["type"]
        # A click without coordinates clicks where the pointer is (e.g. to confirm a G move).
        x, y = action.get("x", self.pointer[0]), action.get("y", self.pointer[1])
        if kind == "click":
            self.click(x, y, action.get("button", "left"))
        elif kind == "double_click":
            self.click(x, y, count=2)
        elif kind == "drag":
            self.drag(action["path"])
        elif kind == "move":
            self.move(action["x"], action["y"])
            time.sleep(0.12)
        elif kind == "scroll":
            self.scroll(x, y, action.get("scroll_x", 0),
                        action.get("scroll_y", 0))
        elif kind == "keypress":
            self.keypress(action["keys"])
        elif kind == "type":
            self.type(action["text"])
        elif kind == "wait":
            time.sleep(action.get("ms", 1000) / 1000)
        elif kind != "screenshot":
            raise ValueError(f"unsupported action {kind!r}")
