"""Error types and short, speakable failure reasons."""
import traceback
from datetime import datetime

from . import config


class TaskFailed(Exception):
    """A task could not be completed. The message is the short reason that is told to the user."""


class Cancelled(Exception):
    """The user answered 'no' to a confirmation question."""


def short_reason(e: BaseException) -> str:
    """One short sentence explaining why something failed (no stack traces, no jargon)."""
    if isinstance(e, TaskFailed):
        return str(e).strip().rstrip(".")
    name = type(e).__name__
    msg = (str(e).strip().splitlines() or [""])[0].strip()
    if name == "TimeoutError":
        return "the page did not respond in time, the website layout may have changed"
    if "has been closed" in msg or "Target closed" in msg or "closed" in msg and "browser" in msg.lower():
        return "the browser window was closed"
    if isinstance(e, ImportError):
        return f"a required package is missing ({getattr(e, 'name', None) or msg})"
    if isinstance(e, PermissionError):
        return "permission was denied"
    return (msg or name)[:140].rstrip(".")


def log_dir():
    d = config.WORKSPACE / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_exception(e: BaseException, context: str = "") -> None:
    """Full details go to a log file; the user only hears the short reason."""
    try:
        with open(log_dir() / "momo_debug.log", "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] {context}\n")
            f.write("".join(traceback.format_exception(type(e), e, e.__traceback__)))
    except Exception:
        pass


def log_line(text: str) -> None:
    """One line in the debug log (used to record what Momo understood from a command)."""
    try:
        with open(log_dir() / "momo_debug.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n")
    except Exception:
        pass
