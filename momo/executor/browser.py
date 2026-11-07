import socket
import subprocess
import time
from urllib.parse import urlparse

from .. import config

# Sites that share one tab: every youtube.com address uses the same YouTube tab, and so on.
FAMILIES = ("youtube.com", "facebook.com", "instagram.com", "linkedin.com",
            "whatsapp.com", "telegram.org", "messenger.com")


def is_blank(url: str) -> bool:
    """New-tab pages of Edge/Chrome (including Edge's start page on ntp.msn.com) count as empty tabs."""
    u = (url or "").lower()
    return (u == "" or u.startswith(("about:", "chrome://newtab", "edge://newtab", "edge://ntp",
                                     "chrome-search://", "chrome://new-tab-page")) or "ntp.msn.com" in u)


def site_key(url: str) -> str:
    host = (urlparse(url).netloc or url).lower()
    host = host[4:] if host.startswith("www.") else host
    for fam in FAMILIES:
        if host == fam or host.endswith("." + fam):
            return fam
    return host


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _start_edge():
    """Start Edge as a normal window (in the logged-in MomoEdge profile) if it is not running.
    The debug port listens on this computer only. Microphone permission is granted automatically only when
    CALL_AUDIO_DEVICE is set (Momo speaks into calls); otherwise Edge asks, so no site can use the mic silently."""
    flags = (f"--remote-debugging-port={config.CDP_PORT} --remote-debugging-address=127.0.0.1 "
             f'--user-data-dir="{config.EDGE_USER_DATA_DIR}" --no-first-run '
             "--autoplay-policy=no-user-gesture-required")
    if config.CALL_AUDIO_DEVICE:
        flags += " --use-fake-ui-for-media-stream"
    subprocess.Popen(f'start "" msedge {flags}', shell=True)
    for _ in range(40):
        if _port_open(config.CDP_PORT):
            return
        time.sleep(0.5)
    raise RuntimeError("Could not start Edge. Open it yourself with start_edge.bat and try again.")


class Browser:
    """Attaches to a running Edge window. Closing Momo does not close Edge.
    Tab rules: an empty new-tab page is used first; a site that is already open in a tab
    (even from an earlier run) is reused; only then a new tab is opened."""

    def __init__(self):
        self._pw = None
        self._browser = None
        self._ctx = None
        self._tabs = {}        # site key -> tab
        self.last_page = None  # most recently used tab (for failure screenshots)

    def _launch(self):
        from playwright.sync_api import sync_playwright
        if self._pw is None:
            self._pw = sync_playwright().start()
        if not _port_open(config.CDP_PORT):
            _start_edge()
        last = None
        for _ in range(5):
            try:
                self._browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{config.CDP_PORT}")
                break
            except Exception as e:
                last = e
                time.sleep(1)
        else:
            raise last
        contexts = self._browser.contexts
        self._ctx = contexts[0] if contexts else self._browser.new_context()
        self._tabs = {}

    def start(self):
        if self._ctx is None:
            self._launch()

    def _open_pages(self):
        return [p for p in self._ctx.pages if not p.is_closed()]

    def _blank_or_new(self):
        for p in self._open_pages():
            if is_blank(p.url):
                return p
        return self._ctx.new_page()

    def _find_open_tab(self, key: str):
        for p in self._open_pages():
            if not is_blank(p.url) and site_key(p.url) == key:
                return p
        return None

    def _with_retry(self, fn):
        self.start()
        try:
            return fn()
        except Exception:
            # Edge was closed: reconnect (and start it again if needed)
            self._ctx = None
            self._browser = None
            self._launch()
            return fn()

    def new_page(self):
        return self._with_retry(self._blank_or_new)

    def page_for(self, url: str):
        """The tab for this site: remembered tab -> already open tab -> empty tab -> new tab."""
        key = site_key(url)

        def get():
            page = self._tabs.get(key)
            if page is not None and (page.is_closed() or not (site_key(page.url) == key or is_blank(page.url))):
                page = None  # closed, or the user has moved that tab to another site
            if page is None:
                page = self._find_open_tab(key) or self._blank_or_new()
            self._tabs[key] = page
            self.last_page = page
            return page

        return self._with_retry(get)

    def close(self):
        # Do not close Edge, only disconnect from it
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = self._browser = self._ctx = None
        self._tabs = {}
