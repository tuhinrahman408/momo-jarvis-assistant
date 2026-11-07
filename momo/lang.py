"""Language helpers. Replies follow the language of the user's command:
English -> English, Bengali script -> Bengali, Banglish (Bengali in English letters) -> Banglish.
Everything the program itself prints is English by default."""
import re

BENGALI_RE = re.compile(r"[\u0980-\u09FF]")
LANGS = ("en", "bn", "banglish")
LANG_NAMES = {
    "en": "English",
    "bn": "Bengali (written in Bengali script)",
    "banglish": "Banglish (Bengali written with English letters)",
}
_ALIASES = {"en": "en", "english": "en", "bn": "bn", "bengali": "bn", "bangla": "bn",
            "banglish": "banglish", "romanized": "banglish"}


def script_lang(text: str) -> str:
    return "bn" if BENGALI_RE.search(text or "") else "en"


def norm_lang(value, text: str = "") -> str:
    """Normalise the model's Reply_Language value, using the text itself as a sanity check."""
    lang = _ALIASES.get(str(value or "").strip().lower())
    has_bengali = bool(BENGALI_RE.search(text or ""))
    if lang is None:
        return script_lang(text)
    if lang == "en" and has_bengali:
        return "bn"
    if lang == "bn" and text and not has_bengali:
        return "banglish"
    return lang


def tts_lang(lang: str) -> str:
    """Speech voice: Bengali voice only for Bengali script, English voice otherwise."""
    return "bn" if lang == "bn" else "en"
