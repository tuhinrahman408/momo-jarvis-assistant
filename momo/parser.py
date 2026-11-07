import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config
from .lang import LANG_NAMES
from .schema import SchemaError, normalize, fallback_clarify
from .skills.registry import catalog_text
from .util import extract_json

_SYSTEM_TEMPLATE = None
_CLIENT = None

# Wake word "Momo" plus its common Bengali-script speech-to-text spellings (written as unicode escapes
# so that this source file stays English-only).
_WAKE = r"(momo|\u09ae\u09ae\u09cb|\u09ae\u09cb\u09ae\u09cb|\u09ae\u09ae\u09be|mama)"
WAKE_RE = re.compile(r"^\s*" + _WAKE + r"(?![\w\u0980-\u09FF])[\s,\u0964.:\-!]*", re.I)
WAKE_RE_ANY = re.compile(_WAKE, re.I)

# If the main model is busy, these models are tried one after another.
FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv(
        "GEMINI_FALLBACK_MODELS",
        "gemini-3.1-flash-lite,gemini-flash-lite-latest,gemini-flash-latest,"
        "gemini-3.5-flash-lite,gemini-2.5-flash-lite",
    ).split(",")
    if m.strip()
]

CHAT_SYSTEM = (
    "You are Momo, a friendly and helpful assistant made by {owner}. Reply in the same language style the "
    "user writes in: Bengali script -> reply in Bengali script; Banglish (Bengali written with English "
    "letters) -> reply in Banglish; English -> reply in English. Keep answers short and clear (under 120 "
    "words) unless the user asks for detail. Never output JSON. Use plain text only: no markdown, no "
    "asterisks, no bullet symbols."
)
TRANSLATE_SYSTEM = (
    "Translate the user's text into {language}. If it is already in that language, return it unchanged. "
    "Keep names, file paths, numbers, URLs and anything in quotation marks exactly as they are. "
    "Output only the translation, as plain text."
)


def current_datetime() -> str:
    return datetime.now(ZoneInfo(config.TIMEZONE)).strftime("%Y-%m-%d %H:%M, %A")


def has_wake_word(text: str) -> bool:
    return bool(WAKE_RE_ANY.search(text[:25]))


def strip_wake_word(text: str) -> str:
    return WAKE_RE.sub("", text.strip(), count=1).strip()


def _system_prompt(now: str) -> str:
    global _SYSTEM_TEMPLATE
    if _SYSTEM_TEMPLATE is None:
        _SYSTEM_TEMPLATE = config.PROMPT_FILE.read_text(encoding="utf-8")
    return (_SYSTEM_TEMPLATE
            .replace("{{CURRENT_DATETIME}}", now)
            .replace("{{SKILL_CATALOG}}", catalog_text()))


def _get_client():
    global _CLIENT
    if _CLIENT is None:
        from google import genai
        from google.genai import types
        key = (config.GEMINI_API_KEY or "").strip()
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set (check your .env file)")
        if not key.isascii():
            raise RuntimeError("GEMINI_API_KEY contains non-ASCII characters. Put only the real key in .env.")
        _CLIENT = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(timeout=15000),  # at most 15 seconds per model
        )
    return _CLIENT


def _generate(contents, gen_config):
    """Try the main model, then the fallbacks if it is busy or missing."""
    client = _get_client()
    models = [config.MODEL] + [m for m in FALLBACK_MODELS if m != config.MODEL]
    last = None
    for model in models:
        try:
            return client.models.generate_content(model=model, contents=contents, config=gen_config)
        except Exception as e:
            code = getattr(e, "code", None)
            timed_out = type(e).__name__ in ("ReadTimeout", "ConnectTimeout", "TimeoutException")
            if code in (404, 429, 500, 503, 504) or timed_out:
                last = e
                print(f"(model {model} did not work [{code or 'timeout'}], trying the next one...)")
                continue
            raise  # wrong key etc.: report it straight away
    raise last


def _call_llm(system: str, user: str) -> str:
    """JSON-mode call used for intent parsing."""
    from google.genai import types
    resp = _generate(
        user,
        types.GenerateContentConfig(system_instruction=system, temperature=0,
                                    response_mime_type="application/json"),
    )
    return resp.text or ""


def generate_text(system: str, user: str, temperature: float = 0.4) -> str:
    """Plain-text call used by the skills (documents, code, summaries ...)."""
    from google.genai import types
    resp = _generate(user, types.GenerateContentConfig(system_instruction=system, temperature=temperature))
    return (resp.text or "").strip()


def localize(text: str, lang: str) -> str:
    """Translate program messages into the user's language. English stays untouched; on any
    failure the English text is returned, so a reply is never lost."""
    if lang == "en" or lang not in LANG_NAMES or not text.strip():
        return text
    try:
        return generate_text(TRANSLATE_SYSTEM.format(language=LANG_NAMES[lang]), text, temperature=0) or text
    except Exception:
        return text


def parse(text: str, llm=_call_llm, now=None) -> dict:
    """Return a validated MOMO dict (the inner object). `llm(system, user) -> str`."""
    text = strip_wake_word(text)[: config.MAX_INPUT_CHARS]
    system = _system_prompt(now or current_datetime())
    for _ in range(2):  # one retry on malformed output
        try:
            return normalize(extract_json(llm(system, text)), text)
        except (SchemaError, ValueError):
            continue
    return fallback_clarify(text)


def chat(text: str, history=None, lang: str = "en", extra_context: str = "") -> str:
    """Normal chat reply. history = [("user", "..."), ("model", "..."), ...]"""
    from google.genai import types
    text = strip_wake_word(text)[:2000]
    contents = [{"role": r, "parts": [{"text": t}]} for r, t in (history or [])]
    contents.append({"role": "user", "parts": [{"text": text}]})
    system = CHAT_SYSTEM.format(owner=config.OWNER_NAME)
    system += f"\nThe user's current message language: {LANG_NAMES.get(lang, 'English')}."
    if extra_context:
        system += "\n" + extra_context
    resp = _generate(contents, types.GenerateContentConfig(system_instruction=system, temperature=0.7))
    return (resp.text or "").strip() or "I cannot answer right now."
