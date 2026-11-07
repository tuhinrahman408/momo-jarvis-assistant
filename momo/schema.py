"""Validation + normalisation of the LLM's JSON. The LLM is never trusted blindly:
Python re-checks enums, platform/action pairs, mandatory fields, skill arguments and rebuilds URLs."""
import re
from urllib.parse import quote_plus

from .lang import norm_lang
from .skills.registry import SKILLS

SUCCESS, NEEDS, UNSUPPORTED = "Success", "Needs_Clarification", "Unsupported"
STATUSES = {SUCCESS, NEEDS, UNSUPPORTED}

PLATFORM_ACTIONS = {
    "WhatsApp": {"Open", "Message", "Call", "Fetch"},
    "Facebook": {"Open", "Message", "Call", "Search", "Fetch"},
    "Instagram": {"Open", "Message"},
    "Telegram": {"Open", "Message", "Call", "Fetch"},
    "LinkedIn": {"Open", "Message", "Search", "Fetch"},
    "Gmail": {"Open", "Message", "Search", "Fetch"},
    "YouTube": {"Open", "Search", "Play"},
    "Calendar": {"Open", "Schedule", "Fetch"},
    "Web": {"Open", "Search", "Fetch"},
    "Local": {"Skill"},          # PC control, files, to-do, office, code, security ... (see skills/)
}
BASE_URLS = {
    "WhatsApp": "https://web.whatsapp.com",
    "Facebook": "https://facebook.com",
    "Instagram": "https://www.instagram.com",
    "Telegram": "https://web.telegram.org",
    "LinkedIn": "https://linkedin.com",
    "Gmail": "https://mail.google.com",
    "YouTube": "https://youtube.com",
    "Calendar": "https://calendar.google.com",
    "Web": "https://google.com",
}
PLATFORMS = set(PLATFORM_ACTIONS) | {"None"}
ACTIONS = {"Open", "Message", "Call", "Search", "Play", "Schedule", "Fetch", "Skill", "None"}
CALL_TYPES = {"Voice", "Video", "None"}
FETCH_RANGES = {"Today", "Tomorrow", "This_Week"}
PARAM_KEYS = ["Search_Query", "Call_Type", "Media_Title", "Time_or_Schedule", "Email_Subject"]
TOP_KEYS = ["Status", "Clarification_Prompt", "Platform", "URL", "Action",
            "Target_Person", "Message_Body", "Skill", "Reply_Language", "Remaining_Request"]
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

GENERIC_CLARIFY = "I did not fully understand your request. Could you say it again?"


class SchemaError(ValueError):
    pass


def _s(v) -> str:
    if v is None:
        return "None"
    v = str(v).strip()
    return v if v else "None"


def is_none(v) -> bool:
    return v == "None"


def _clean_args(skill_name: str, raw) -> dict:
    """Keep only the arguments the skill declares; drop empty values."""
    sk = SKILLS.get(skill_name)
    if not sk or not isinstance(raw, dict):
        return {}
    allowed = set(sk.required) | set(sk.optional)
    return {k: str(v).strip() for k, v in raw.items()
            if k in allowed and v is not None and str(v).strip() not in ("", "None")}


def normalize(raw: dict, text: str = "") -> dict:
    """Takes {"MOMO": {...}} and returns the validated inner dict. `text` is the user's command."""
    m = raw.get("MOMO") if isinstance(raw, dict) else None
    if not isinstance(m, dict):
        raise SchemaError("missing MOMO object")

    out = {k: _s(m.get(k)) for k in TOP_KEYS}
    params_in = m.get("Additional_Params")
    params_in = params_in if isinstance(params_in, dict) else {}
    out["Additional_Params"] = {k: _s(params_in.get(k)) for k in PARAM_KEYS}
    out["Reply_Language"] = norm_lang(out["Reply_Language"], text)
    out["Skill_Args"] = _clean_args(out["Skill"], m.get("Skill_Args"))
    out["Prompt_Source"] = "llm"  # becomes "system" when Python writes the clarification itself

    if out["Status"] not in STATUSES:
        raise SchemaError(f"bad Status {out['Status']}")
    if out["Platform"] not in PLATFORMS:
        raise SchemaError(f"bad Platform {out['Platform']}")
    if out["Action"] not in ACTIONS:
        raise SchemaError(f"bad Action {out['Action']}")
    if out["Additional_Params"]["Call_Type"] not in CALL_TYPES:
        out["Additional_Params"]["Call_Type"] = "None"

    if not is_none(out["Skill"]):  # tolerate a missing Action/Platform when a skill is named
        if out["Action"] == "None":
            out["Action"] = "Skill"
        if out["Platform"] == "None":
            out["Platform"] = "Local"

    out["URL"] = _clean_url(out["URL"])
    if out["Action"] == "Open" and out["Platform"] == "None" and not is_none(out["URL"]):
        out["Platform"] = "Web"

    if out["Status"] == SUCCESS:
        problem = _check_success(out)
        if problem:
            out["Status"] = NEEDS
            if is_none(out["Clarification_Prompt"]):
                out["Clarification_Prompt"], out["Prompt_Source"] = problem, "system"
        else:
            out["Clarification_Prompt"] = "None"
            out["URL"] = build_url(out)
    elif is_none(out["Clarification_Prompt"]):
        out["Clarification_Prompt"], out["Prompt_Source"] = GENERIC_CLARIFY, "system"
    return out


def _clean_url(url: str) -> str:
    if is_none(url):
        return url
    if re.match(r"^(javascript|data|file|vbscript):", url, re.I):
        return "None"
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url


def _check_success(c: dict):
    p, a, ap = c["Platform"], c["Action"], c["Additional_Params"]
    if a == "None":
        return GENERIC_CLARIFY
    if a == "Play" and p == "None":
        c["Platform"] = p = "YouTube"
    if a == "Schedule" and p == "None":
        c["Platform"] = p = "Calendar"
    if p == "None":
        return "Which platform should I use?"
    if a not in PLATFORM_ACTIONS[p]:
        return f"I cannot do that on {p}. Which other app would you like to use?"
    if a == "Skill":
        sk = SKILLS.get(c["Skill"])
        if not sk:
            return "I do not know how to do that yet. What would you like me to do?"
        missing = [k for k in sk.required if k not in c["Skill_Args"]]
        if missing:
            return f"Please tell me the {missing[0].replace('_', ' ')}."
    elif a == "Message":
        if is_none(c["Target_Person"]):
            return "Who should I send it to?"
        if is_none(c["Message_Body"]):
            return "What message should I send?"
    elif a == "Call":
        if is_none(c["Target_Person"]):
            return "Who should I call?"
    elif a == "Search":
        if is_none(ap["Search_Query"]):
            return "What should I search for?"
    elif a == "Play":
        if is_none(ap["Media_Title"]):
            return "What should I play?"
    elif a == "Schedule":
        if is_none(c["Message_Body"]):
            return "What is the event called?"
        if not TIME_RE.match(ap["Time_or_Schedule"]):
            return "Which date and what time should I set it for?"
    elif a == "Open":
        if p == "Web" and is_none(c["URL"]):
            return "Which website should I open?"
    elif a == "Fetch":
        if ap["Time_or_Schedule"] not in FETCH_RANGES:
            ap["Time_or_Schedule"] = "Today" if p == "Calendar" else "None"
    return None


def build_url(c: dict) -> str:
    p, a, ap = c["Platform"], c["Action"], c["Additional_Params"]
    if a == "Skill":
        return "None"
    if a == "Play":
        return "https://www.youtube.com/results?search_query=" + quote_plus(ap["Media_Title"])
    if a == "Search":
        q = quote_plus(ap["Search_Query"])
        return {
            "YouTube": f"https://www.youtube.com/results?search_query={q}",
            "Web": f"https://www.google.com/search?q={q}",
            "Facebook": f"https://www.facebook.com/search/top?q={q}",
            "LinkedIn": f"https://www.linkedin.com/search/results/all/?keywords={q}",
            "Gmail": f"https://mail.google.com/mail/u/0/#search/{q}",
        }.get(p, BASE_URLS.get(p, "None"))
    if p == "Web" and a in ("Open", "Fetch") and not is_none(c["URL"]):
        return c["URL"]
    return BASE_URLS.get(p, "None")


def fallback_clarify(text: str = "") -> dict:
    out = normalize({"MOMO": {"Status": NEEDS, "Clarification_Prompt": GENERIC_CLARIFY}}, text)
    out["Prompt_Source"] = "system"
    return out
