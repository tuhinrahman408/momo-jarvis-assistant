"""Coloured terminal output (used by the text mode and for messages after the window is closed)."""
import os

_CODES = {"momo": "\033[92m", "status": "\033[96m", "confirm": "\033[93m", "error": "\033[91m",
          "prompt": "\033[1;95m", "dim": "\033[90m"}
_RESET = "\033[0m"
_ready = False


def enable_ansi() -> None:
    global _ready
    if not _ready:
        if os.name == "nt":
            os.system("")  # switches on ANSI colour support in the Windows terminal
        _ready = True


def paint(text: str, kind: str) -> str:
    enable_ansi()
    return f"{_CODES.get(kind, '')}{text}{_RESET}"


def show(text: str, kind: str = "momo") -> None:
    print(paint(f"[Momo] {text}", kind))


def divider() -> None:
    print(paint("-" * 56, "dim"))
