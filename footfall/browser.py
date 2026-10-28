"""A real Chrome on a private display, one per persona, with a throwaway profile.

The persona only sees pixels and acts with mouse and keyboard. The harness reads the current
URL through the DevTools HTTP endpoint (read-only) to follow progress through the funnel.
"""

import json
import shutil
import socket
import tempfile
import time
import urllib.request

CHROME = shutil.which("google-chrome") or shutil.which("chromium") or "google-chrome"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Browser:
    def __init__(self, desktop, url):
        self.desktop, self.start_url = desktop, url
        self.port = free_port()
        self.profile = tempfile.mkdtemp(prefix="footfall-chrome-")

    def launch(self):
        w, h = self.desktop.size
        self.proc = self.desktop.launch([
            CHROME, f"--user-data-dir={self.profile}", f"--remote-debugging-port={self.port}",
            "--no-first-run", "--no-default-browser-check", "--disable-sync",
            "--disable-features=Translate,MediaRouter,OptimizationHints",
            "--password-store=basic", "--disable-session-crashed-bubble",
            "--window-position=0,0", f"--window-size={w},{h}", "--force-device-scale-factor=1",
            self.start_url], "chrome.log")
        return self

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
        if getattr(self, "proc", None) and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except Exception:
                self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)
