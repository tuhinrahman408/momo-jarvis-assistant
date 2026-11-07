"""Open Chrome or Edge with a chosen profile, for people who use several Google accounts.
Profiles are read from the browser's own 'Local State' file; a profile is matched by its display
name, Google account name or email, e.g. 'asif', 'tuhin' or 'daffodil'."""
import json
import os
import subprocess
import sys
from pathlib import Path

from ..errors import TaskFailed
from .registry import skill

IS_WIN = sys.platform.startswith("win")
BROWSERS = {
    "chrome": {"exe": "chrome.exe", "dirs": [r"Google\Chrome\Application"], "state": r"Google\Chrome\User Data\Local State"},
    "edge": {"exe": "msedge.exe", "dirs": [r"Microsoft\Edge\Application"], "state": r"Microsoft\Edge\User Data\Local State"},
}


def _browser(name: str) -> dict:
    key = (name or "chrome").strip().lower()
    key = {"google chrome": "chrome", "microsoft edge": "edge"}.get(key, key)
    if key not in BROWSERS:
        raise TaskFailed("I can open Chrome or Edge profiles only")
    return key, BROWSERS[key]


def _state_path(info: dict) -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / info["state"]


def _exe_path(info: dict) -> str:
    roots = [os.environ.get(v) for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    for root in filter(None, roots):
        for d in info["dirs"]:
            p = Path(root) / d / info["exe"]
            if p.exists():
                return str(p)
    if IS_WIN:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{info['exe']}") as k:
                return winreg.QueryValue(k, None)
        except OSError:
            pass
    raise TaskFailed(f"I could not find {info['exe']} on this computer")


def read_profiles(info: dict) -> list:
    """[(folder, [names that can match])] for every profile of the browser."""
    try:
        state = json.loads(_state_path(info).read_text(encoding="utf-8"))
        cache = state["profile"]["info_cache"]
    except Exception:
        raise TaskFailed("I could not read the browser profile list")
    out = []
    for folder, p in cache.items():
        names = [str(p.get(k, "")) for k in ("name", "gaia_name", "given_name", "user_name") if p.get(k)]
        out.append((folder, names))
    return out


def find_profile(profiles: list, wanted: str) -> str:
    w = wanted.strip().lower()
    exact = [f for f, names in profiles if w in [n.lower() for n in names] or w == f.lower()]
    if len(exact) == 1:
        return exact[0]
    partial = [f for f, names in profiles if any(w in n.lower() for n in names)]
    hits = exact or partial
    if len(hits) == 1:
        return hits[0]
    listing = "; ".join(f"{names[0] if names else f}" for f, names in profiles)
    if not hits:
        raise TaskFailed(f"there is no profile named {wanted}. Profiles found: {listing}")
    raise TaskFailed(f"several profiles match {wanted}. Please be more specific. Profiles: {listing}")


@skill("open_browser", optional=("profile", "browser", "url"),
       doc="Open Chrome (or Edge) with a specific profile or Google account, e.g. profile = asif, tuhin or daffodil, optionally opening a url")
def open_browser(args, ctx):
    key, info = _browser(args.get("browser", "chrome"))
    cmd = [_exe_path(info)]
    profile = args.get("profile", "").strip()
    if profile:
        cmd.append(f"--profile-directory={find_profile(read_profiles(info), profile)}")
    url = args.get("url", "").strip()
    if url:
        cmd.append(url if url.startswith(("http://", "https://")) else "https://" + url)
    subprocess.Popen(cmd)
    return f"Opened {key}" + (f" with the {profile} profile." if profile else ".")


@skill("list_browser_profiles", optional=("browser",), doc="Tell which Chrome (or Edge) profiles exist on this computer")
def list_browser_profiles(args, ctx):
    key, info = _browser(args.get("browser", "chrome"))
    profiles = read_profiles(info)
    return f"{key} profiles: " + "; ".join(names[0] if names else f for f, names in profiles)
