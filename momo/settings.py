"""Settings changed in the window are saved here and applied again at the next start."""
import json

from . import config

# window setting -> (config attribute, type)
KEYS = {"voice_reply": ("VOICE_REPLY", bool), "confirm": ("CONFIRM_BEFORE_SEND", bool),
        "voice_engine": ("VOICE_ENGINE", str)}


def _file():
    return config.DATA_DIR / "settings.json"


def load() -> None:
    """Apply the saved settings to the running program."""
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except Exception:
        return
    for key, (attr, kind) in KEYS.items():
        if key in data and isinstance(data[key], kind):
            setattr(config, attr, data[key])


def update(key: str, value) -> None:
    attr, kind = KEYS[key]
    setattr(config, attr, kind(value))
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data[key] = kind(value)
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _file().write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass