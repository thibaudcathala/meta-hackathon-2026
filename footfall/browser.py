"""A real Chrome on a private display, one per persona, with a throwaway profile.

The persona only sees pixels and acts with mouse and keyboard. The harness reads the current
URL through the DevTools HTTP endpoint (read-only) to follow progress through the funnel.

Chrome will not shrink its window below about 500 pixels, so a phone is emulated through
DevTools instead: the page gets a phone-sized viewport, touch and a mobile user agent, and the
private display is cut to the phone's width. The DevTools connection stays open for the whole
session, since Chrome drops the emulation when it closes.
"""

import json
import shutil
import socket
import tempfile
import time
import urllib.request

import websocket
from pathlib import Path

CHROME = shutil.which("google-chrome") or shutil.which("chromium") or "google-chrome"
MOBILE_UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/130.0.0.0 Mobile Safari/537.36")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Browser:
    def __init__(self, desktop, url, mobile=False):
        self.desktop, self.start_url, self.mobile = desktop, url, mobile
        self.port = free_port()
        self.profile = tempfile.mkdtemp(prefix="footfall-chrome-")

    def launch(self):
        # Chrome's own bubbles ("Save address?", "Save password?") are not the site's doing:
        # switch them off so every friction a persona meets belongs to the site.
        default = Path(self.profile) / "Default"
        default.mkdir(parents=True, exist_ok=True)
        (default / "Preferences").write_text(json.dumps({
            "autofill": {"profile_enabled": False, "credit_card_enabled": False},
            "credentials_enable_service": False,
            "profile": {"password_manager_enabled": False},
            "translate": {"enabled": False}}))
        w, h = self.desktop.size
        self.proc = self.desktop.launch([
            CHROME, f"--user-data-dir={self.profile}", f"--remote-debugging-port={self.port}",
            "--no-first-run", "--no-default-browser-check", "--disable-sync",
            "--disable-features=Translate,MediaRouter,OptimizationHints",
            "--password-store=basic", "--disable-session-crashed-bubble",
            "--window-position=0,0", f"--window-size={w},{h}", "--force-device-scale-factor=1",
            *([f"--user-agent={MOBILE_UA}", "--touch-events=enabled"] if self.mobile else []),
            self.start_url], "chrome.log")
        return self

    def emulate(self, chrome_height=88):
        """Give the page the screen's size as a mobile viewport (call once it has loaded)."""
        w, h = self.desktop.size
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=5) as r:
            page = next(p for p in json.load(r) if p.get("type") == "page")
        self.devtools = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=10)
        for i, (method, params) in enumerate([
                ("Emulation.setDeviceMetricsOverride", {"width": w, "height": h - chrome_height,
                                                        "deviceScaleFactor": 1, "mobile": True}),
                ("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5}),
                ("Emulation.setEmitTouchEventsForMouse", {"enabled": True,
                                                          "configuration": "mobile"}),
                ("Page.reload", {})]):
            self.devtools.send(json.dumps({"id": i + 1, "method": method, "params": params}))
            while json.loads(self.devtools.recv()).get("id") != i + 1:
                pass
        time.sleep(1)
        self.desktop.settle(quiet=1.0, timeout=15)

    def url(self):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=2) as r:
                pages = [p for p in json.load(r) if p.get("type") == "page"]
        except OSError:
            return None
        return pages[0]["url"] if pages else None

    def wait_loaded(self, timeout=60):
        start = time.time()
        while time.time() - start < timeout:
            url = self.url()
            if url and url.startswith(self.start_url.rstrip("/")):
                self.desktop.settle(quiet=1.0, timeout=15)
                return True
            time.sleep(0.5)
        return False

    def close(self):
        if getattr(self, "devtools", None):
            self.devtools.close()
        if getattr(self, "proc", None) and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except Exception:
                self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)
