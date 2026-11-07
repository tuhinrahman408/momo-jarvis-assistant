import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # dotenv is optional
    pass

ROOT = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "y")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# --- AI model -------------------------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Dhaka")

# --- Speech ---------------------------------------------------------------
# Several languages are tried for every utterance; the most confident result wins.
STT_LANGS = [x.strip() for x in os.getenv("STT_LANGS", "en-US,bn-BD").split(",") if x.strip()]
REQUIRE_WAKE_WORD = _bool("REQUIRE_WAKE_WORD", False)   # text-mode voice only; the window needs no wake word
VOICE_REPLY = _bool("VOICE_REPLY", True)                 # Momo speaks every reply
VOICE_ENGINE = os.getenv("VOICE_ENGINE", "auto").strip().lower()   # auto | edge | sapi | gtts
VOICE_EN = os.getenv("VOICE_EN", "en-GB-RyanNeural")     # English neural voice (edge)
VOICE_BN = os.getenv("VOICE_BN", "bn-BD-NabanitaNeural") # Bengali neural voice (edge)

# --- Behaviour ------------------------------------------------------------
CONFIRM_BEFORE_SEND = _bool("CONFIRM_BEFORE_SEND", True)
HEADLESS = _bool("HEADLESS", False)
MAX_INPUT_CHARS = 500
MAX_CLARIFY_ROUNDS = 2
MAX_CHAINED_ACTIONS = 5

# --- Calling system -------------------------------------------------------
OWNER_NAME = os.getenv("OWNER_NAME", "Tuhin vai")            # used in the spoken call intro
CALL_AUDIO_DEVICE = os.getenv("CALL_AUDIO_DEVICE", "")       # e.g. "CABLE Input" (empty = default speakers)
CALL_ANSWER_TIMEOUT = _int("CALL_ANSWER_TIMEOUT", 45)        # seconds to wait for the callee to pick up
CALL_FALLBACK_WAIT = _int("CALL_FALLBACK_WAIT", 15)          # used when ringing cannot be detected

# --- Files ----------------------------------------------------------------
PROMPT_FILE = ROOT / "prompts" / "system_prompt.txt"
CONTACTS_FILE = ROOT / "contacts.json"
APPS_FILE = ROOT / "apps.json"                               # optional custom apps: {"name": "path.exe"}
WORKSPACE = Path(os.getenv("MOMO_WORKSPACE", str(Path.home() / "Momo")))
DATA_DIR = WORKSPACE / "data"

# --- Browser --------------------------------------------------------------
PROFILE_DIR = ROOT / "browser_profile"
CDP_PORT = _int("EDGE_DEBUG_PORT", 9222)
EDGE_USER_DATA_DIR = os.getenv("EDGE_USER_DATA_DIR", str(Path.home() / "MomoEdge"))
