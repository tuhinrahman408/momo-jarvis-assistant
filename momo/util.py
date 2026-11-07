"""Small shared helpers."""
import json
import re
import unicodedata

from . import config


def extract_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found")
    return json.loads(text[start:end + 1])


def strip_code_fences(text: str) -> str:
    m = re.search(r"```[\w+-]*\n(.*?)```", text, re.S)
    return ((m.group(1) if m else text).strip("\n")) + "\n"


def safe_name(name: str, ext: str = "") -> str:
    base = re.sub(r"[^\w.-]+", "_", name.strip(), flags=re.UNICODE).strip("._") or "untitled"
    base = base[: 80 - len(ext)]
    return base if (ext and base.lower().endswith(ext.lower())) else base + ext


# Words people put before or after a name ("Rakib vai", "Rahim apu"). They are never part of the name.
# Bengali words are written as unicode escapes so this file stays English-only.
HONORIFICS = {"bhai", "vai", "bhaiya", "bhaia", "apu", "apa", "sir", "madam", "mam", "bro", "dada", "didi",
              "uncle", "aunty", "\u09ad\u09be\u0987", "\u09ad\u09be\u0987\u09af\u09bc\u09be", "\u0986\u09aa\u09c1",
              "\u0986\u09aa\u09ba", "\u09b8\u09cd\u09af\u09be\u09b0"}


def norm(text: str) -> str:
    """Lower-case, no punctuation or emoji, single spaces. Letters, marks and digits of every script stay
    (Bengali vowel signs are marks, so they must not be removed)."""
    s = unicodedata.normalize("NFKC", text or "").casefold()
    return " ".join("".join(c if unicodedata.category(c)[0] in "LMN" else " " for c in s).split())


def name_tokens(name: str) -> list:
    words = norm(name).split()
    return [w for w in words if w not in HONORIFICS] or words


def strip_honorifics(name: str) -> str:
    words = (name or "").split()
    kept = [w for w in words if norm(w) not in HONORIFICS]
    return " ".join(kept or words).strip()


def name_score(query: str, title: str) -> int:
    """How well a chat/contact title fits the name the user said.
    3 = same name, 2 = every word of the query is a whole word of the title ("Rakib" in "Rakib Hasan"),
    0 = no match. "Rakib" does NOT match "Rakibul": that is a different person."""
    q, t = name_tokens(query), norm(title).split()
    if not q or not t:
        return 0
    if q == [w for w in t if w not in HONORIFICS]:
        return 3
    return 2 if all(w in t for w in q) else 0


def contacts() -> dict:
    try:
        data = json.loads(config.CONTACTS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def find_contact(name: str):
    """(key, entry) of the person in contacts.json, or (None, None).
    An entry is an email string (old format) or an object such as
    {"email": "...", "whatsapp": "+8801...", "telegram": "@user", "facebook": "user.name", "aliases": ["..."]}.
    The person is found by key or alias. If two different people fit equally well nobody is returned:
    Momo never guesses between people."""
    best, found = 0, []
    for key, value in contacts().items():
        names = [key] + (list(value.get("aliases", [])) if isinstance(value, dict) else [])
        score = max((name_score(name, n) for n in names if isinstance(n, str)), default=0)
        if score > best:
            best, found = score, [key]
        elif score and score == best:
            found.append(key)
    if best == 0 or len(found) != 1:
        return None, None
    entry = contacts()[found[0]]
    return found[0], ({"email": entry} if isinstance(entry, str) else entry)


def contact(name: str, field: str):
    """One field (email, whatsapp, telegram, facebook ...) of a person from contacts.json, else None."""
    _, entry = find_contact(name)
    return entry.get(field) if entry else None


def contact_key(name: str) -> str:
    """The name used in contacts.json (so a Bengali spelling or 'Rakib vai' finds 'Rakib'), else the spoken
    name without 'vai/apu/sir'."""
    key, _ = find_contact(name)
    return key or strip_honorifics(name)


def wa_digits(phone: str) -> str:
    """Phone number for WhatsApp: digits only, with the country code. 01712345678 -> 8801712345678."""
    d = re.sub(r"\D", "", phone or "")
    if d.startswith("00"):
        d = d[2:]
    if len(d) == 11 and d.startswith("01"):
        d = "88" + d
    return d


def open_url(browser, url: str):
    page = browser.page_for(url)
    page.goto(url, wait_until="domcontentloaded")
    page.bring_to_front()
    return page
