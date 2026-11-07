"""Platform handlers. Web pages change often, so every step tries several selectors and, when nothing
matches, raises TaskFailed with a short reason (the full details go to the log file).
Handlers return a short English sentence; the orchestrator translates it when needed."""
import re
import time
from datetime import datetime, timedelta
from urllib.parse import quote, quote_plus
from zoneinfo import ZoneInfo

from .. import audio, config
from ..errors import Cancelled, TaskFailed
from ..lang import script_lang
from ..parser import localize
from ..schema import is_none
from ..skills import run_skill
from ..util import contact, contact_key, name_score, open_url, wa_digits
from . import chats, desktop_call

RINGING = re.compile(r"\b(ringing|calling|connecting)\b", re.I)
CALL_FAILED = re.compile(r"\b(call ended|declined|no answer|unavailable|couldn.t connect|busy)\b", re.I)

# ---- selectors (edit here when a website changes) ----
WA_SEARCH = ['[data-testid="chat-list-search"]', 'div[contenteditable="true"][data-tab="3"]',
             '#side div[contenteditable="true"]', 'div[role="textbox"][aria-label*="Search" i]',
             'div[contenteditable="true"][aria-label*="Search" i]']
WA_RESULTS = ['#pane-side [role="listitem"]', '#pane-side [role="row"]', '#pane-side [role="gridcell"]',
              '[data-testid="cell-frame-container"]']
WA_COMPOSE = ['footer div[contenteditable="true"]', 'div[contenteditable="true"][aria-placeholder*="message" i]',
              'div[contenteditable="true"][data-tab="10"]', '[data-testid="conversation-compose-box-input"]']
WA_QR = ['canvas[aria-label*="QR" i]', '[data-testid="qrcode"]', 'div[data-ref]']
WA_TITLE = ['#main header span[dir="auto"]', '#main header [title]', 'header span[title]']
TG_SEARCH = ['input.input-search-input', 'input[type="search"]']
TG_RESULTS = ['ul.chatlist a', '.search-group a', 'a.chatlist-chat']
TG_COMPOSE = ['div.input-message-input[contenteditable="true"]', 'div[contenteditable="true"]']
TG_LOGIN = ['canvas.qr-canvas', '.auth-image', 'button.btn-primary:has-text("Log in")']
FB_SEARCH = ['input[type="search"]', 'input[placeholder*="Search Messenger" i]',
             'input[aria-label*="Search Messenger" i]', 'input[aria-label*="Search" i]']
FB_RESULTS = ['a[href*="/messages/t/"]', '[role="option"]', '[role="listbox"] [role="button"]',
              '[role="grid"] a', 'ul[role="listbox"] li']
FB_COMPOSE = ['div[role="textbox"][contenteditable="true"][aria-label*="Message" i]',
              'div[role="textbox"][contenteditable="true"]']
FB_LOGIN = ['input[name="email"]', 'form[action*="login"]', 'button[name="login"]']
# Header (chat title) selectors. Only the ones that are reliable are listed: when the header cannot be read,
# the name of the row that was clicked is trusted. Add a selector for Facebook / Instagram / LinkedIn if you want.
TG_TITLE = ['.chat-info .peer-title', '.top .peer-title', '.chat-info-container .peer-title']
FB_TITLE, IG_TITLE, LI_TITLE = [], [], []
POPUP_BUTTONS = re.compile(r"^(not now|maybe later|no thanks|not interested|dismiss)$", re.I)
EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
IG_SEARCH = ['input[name="queryBox"]', 'input[placeholder*="Search" i]']
IG_COMPOSE = ['div[role="textbox"][contenteditable="true"]', 'textarea[placeholder*="Message" i]']
IG_LOGIN = ['input[name="username"]', 'button[type="submit"]:has-text("Log in")']


def _type(page, text):
    page.keyboard.type(text, delay=25)


def _send_text(page, box, text):
    """Put the whole message into the box at once (reliable for Bengali text), then press Enter."""
    box.click()
    page.keyboard.insert_text(text)
    page.wait_for_timeout(300)
    page.keyboard.press("Enter")


def _first_visible(page, selectors, timeout_ms=15000, what="the element"):
    """Return the first locator (from a list of selectors or locator-builders) that becomes visible.
    On failure the visible controls of the page are written to the log, so a changed website is easy to fix."""
    end = time.time() + timeout_ms / 1000
    while time.time() < end:
        for sel in selectors:
            try:
                loc = (page.locator(sel) if isinstance(sel, str) else sel(page)).first
                if loc.is_visible():
                    return loc
            except Exception:
                pass
        page.wait_for_timeout(300)
    chats.dump_labels(page, what)
    raise TaskFailed(f"I could not find {what} on the page")


def _dismiss_popups(page):
    """Close 'Turn on notifications' style pop-ups that cover the page (only harmless 'Not now' buttons)."""
    try:
        btn = page.get_by_role("button", name=POPUP_BUTTONS).first
        if btn.is_visible():
            btn.click(timeout=1500)
    except Exception:
        pass


def _wait_ready(page, ready, login, name, timeout=40):
    """Wait until the site is usable; if a login page shows up instead, say so."""
    start = time.time()
    while time.time() - start < timeout:
        for sel in ready:
            try:
                if page.locator(sel).first.is_visible():
                    _dismiss_popups(page)
                    return
            except Exception:
                pass
        if time.time() - start > 6:
            for sel in login:
                try:
                    if page.locator(sel).first.is_visible():
                        raise TaskFailed(f"{name} is not logged in. Log in once in the Momo browser window")
                except TaskFailed:
                    raise
                except Exception:
                    pass
        page.wait_for_timeout(500)
    raise TaskFailed(f"{name} did not finish loading")


def _pick_row(page, row_selectors, who, confirm, what, timeout_ms=10000, title_lines=1):
    """Click the chat whose NAME is `who` and return its title (see chats.py for the safety rules)."""
    return chats.pick_chat(page, row_selectors, who, confirm, what, timeout_ms, title_lines)


def _clear_and_type(page, box, text):
    box.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    _type(page, text)


def _page_title(page, selectors) -> str:
    return chats.header_title(page, selectors)


# ---------------- Open / Search / Play ----------------
def h_open(cmd, browser, confirm, ctx):
    open_url(browser, cmd["URL"])
    return f"Opened {cmd['Platform']}."


def h_search(cmd, browser, confirm, ctx):
    open_url(browser, cmd["URL"])
    return f"Searched for \"{cmd['Additional_Params']['Search_Query']}\"."


def h_play(cmd, browser, confirm, ctx):
    page = open_url(browser, cmd["URL"])  # the same YouTube tab is reused every time
    page.wait_for_selector("ytd-video-renderer a#video-title", timeout=15000)
    page.locator("ytd-video-renderer a#video-title").first.click()
    return f"Playing {cmd['Additional_Params']['Media_Title']}."


# ---------------- Opening a chat with a person ----------------
# Every opener returns (page, title): the chat is open and `title` is the name that was really found.
# A person who is in contacts.json with a number / username is opened directly (no search, no guessing).
def _chat_whatsapp(b, who, confirm):
    phone = wa_digits(contact(who, "whatsapp") or "")
    page = open_url(b, f"https://web.whatsapp.com/send?phone={phone}" if phone else "https://web.whatsapp.com")
    _wait_ready(page, WA_SEARCH + WA_COMPOSE, WA_QR, "WhatsApp")
    if phone:  # the number is in contacts.json: the chat opens directly
        _first_visible(page, WA_COMPOSE, 20000, "the WhatsApp message box (is the number in contacts.json correct?)")
        return page, f"{chats.header_title(page, WA_TITLE) or who} (+{phone})"
    box = _first_visible(page, WA_SEARCH, 15000, "the WhatsApp search box")
    _clear_and_type(page, box, who)
    title = _pick_row(page, WA_RESULTS, who, confirm, "WhatsApp")
    _first_visible(page, WA_COMPOSE, 12000, f"the chat with {who} (check the contact name)")
    return page, chats.verify_header(page, WA_TITLE, who, title, confirm)


def _chat_telegram(b, who, confirm):
    user = str(contact(who, "telegram") or "").strip().lstrip("@")
    if re.fullmatch(r"[A-Za-z0-9_]{4,32}", user):  # username in contacts.json: open the chat directly
        page = open_url(b, f"https://web.telegram.org/k/#@{user}")
        _wait_ready(page, TG_COMPOSE, TG_LOGIN, "Telegram")
        page.wait_for_timeout(1200)
        return page, f"{chats.header_title(page, TG_TITLE) or who} (@{user})"
    page = open_url(b, "https://web.telegram.org/k/")
    _wait_ready(page, TG_SEARCH, TG_LOGIN, "Telegram")
    box = _first_visible(page, TG_SEARCH, 15000, "the Telegram search box")
    _clear_and_type(page, box, who)
    title = _pick_row(page, TG_RESULTS, who, confirm, "Telegram")
    page.wait_for_timeout(800)
    return page, chats.verify_header(page, TG_TITLE, who, title, confirm)


def _chat_facebook(b, who, confirm):
    fid = str(contact(who, "facebook") or "").strip()
    if re.fullmatch(r"[\w.\-]{3,80}", fid):  # profile username / id in contacts.json: open the chat directly
        page = open_url(b, f"https://www.facebook.com/messages/t/{fid}")
        _wait_ready(page, FB_COMPOSE, FB_LOGIN, "Facebook")
        return page, f"{who} (facebook: {fid})"
    page = open_url(b, "https://www.facebook.com/messages/t/")
    _wait_ready(page, FB_SEARCH, FB_LOGIN, "Facebook")
    box = _first_visible(page, FB_SEARCH, 15000, "the Messenger search box")
    _clear_and_type(page, box, who)
    title = _pick_row(page, FB_RESULTS, who, confirm, "Facebook")
    page.wait_for_timeout(1000)
    return page, chats.verify_header(page, FB_TITLE, who, title, confirm)


IG_RESULTS = ['div[role="dialog"] div[role="button"]', 'div[role="dialog"] [role="listitem"]', 'div[role="dialog"] label']


def _chat_instagram(b, who, confirm):
    page = open_url(b, "https://www.instagram.com/direct/new/")
    _wait_ready(page, IG_SEARCH, IG_LOGIN, "Instagram")
    box = _first_visible(page, IG_SEARCH, 15000, "the Instagram search box")
    _clear_and_type(page, box, who)
    # the name line or the username line of a result may be the one the user said
    title = _pick_row(page, IG_RESULTS, who, confirm, "Instagram", title_lines=2)
    page.get_by_role("button", name=re.compile(r"^(Chat|Next)$", re.I)).first.click(timeout=8000)
    page.wait_for_timeout(1000)
    return page, chats.verify_header(page, IG_TITLE, who, title, confirm)


def _chat_linkedin(b, who, confirm):
    page = open_url(b, "https://www.linkedin.com/messaging/")
    box = _first_visible(page, ['input[placeholder*="Search messages" i]', 'input[aria-label*="Search messages" i]'],
                         30000, "the LinkedIn search box")
    _clear_and_type(page, box, who)
    page.keyboard.press("Enter")
    title = _pick_row(page, ["li.msg-conversation-listitem", ".msg-conversation-card"], who, confirm, "LinkedIn")
    return page, chats.verify_header(page, LI_TITLE, who, title, confirm)


OPEN_CHAT = {"WhatsApp": _chat_whatsapp, "Telegram": _chat_telegram, "Facebook": _chat_facebook,
             "Instagram": _chat_instagram, "LinkedIn": _chat_linkedin}
COMPOSE = {"WhatsApp": WA_COMPOSE, "Telegram": TG_COMPOSE, "Facebook": FB_COMPOSE, "Instagram": IG_COMPOSE,
           "LinkedIn": ["div.msg-form__contenteditable"]}


def _open_chat(platform, browser, who, confirm):
    """Open the chat of `who` and return (page, real chat title). Nothing is sent or called here.
    The name is first looked up in contacts.json (also by alias, so 'Rakib vai' finds 'Rakib')."""
    term = contact_key(who)
    result = OPEN_CHAT[platform](browser, term, confirm)
    return result if isinstance(result, tuple) else (result, term)


def _to_line(title, who) -> str:
    """'To: ...' line of a confirmation card: the real chat name, and what the user said if it differs."""
    return f"{title}" if name_score(who, title) else f"{title} (you said '{who}')"


def _verify_sent(page, selectors, body) -> bool:
    """Best effort: the message box is empty again and the text now shows in the page."""
    probe = re.sub(r"\s+", " ", body).strip()[:40]
    end = time.time() + 6
    while time.time() < end:
        box_text = None
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.is_visible():
                    box_text = loc.inner_text() or ""
                    break
            except Exception:
                pass
        try:
            text = re.sub(r"\s+", " ", page.inner_text("body"))
        except Exception:
            text = ""
        if box_text is not None and not box_text.strip() and probe in text:
            return True
        page.wait_for_timeout(500)
    return False


# ---------------- Message ----------------
def h_message(cmd, browser, confirm, ctx):
    p, who, body = cmd["Platform"], cmd["Target_Person"], cmd["Message_Body"]
    if p == "Gmail":
        return _msg_gmail(cmd, browser, confirm, who, body)
    if p not in OPEN_CHAT:
        raise TaskFailed(f"messages on {p} are not supported")
    page, title = _open_chat(p, browser, who, confirm)  # the right chat is open, nothing is sent yet
    if config.CONFIRM_BEFORE_SEND and not confirm(
            f"Send this {p} message?\nTo: {_to_line(title, who)}\nMessage: {body}\nSay or tap yes to send."):
        raise Cancelled()
    box = _first_visible(page, COMPOSE[p], 12000, "the message box")
    _send_text(page, box, body)
    if _verify_sent(page, COMPOSE[p], body):
        return f"Sent your message to {title} on {p}."
    return (f"I pressed send to {title} on {p}, but I could not confirm that it was delivered. "
            "Please check the chat.")


def _msg_gmail(cmd, b, confirm, who, body):
    to = who.strip() if "@" in who else contact(who, "email")
    if not to:
        raise TaskFailed(f"there is no email address for {who} in contacts.json")
    if not EMAIL_RE.match(to):
        raise TaskFailed("that is not one valid email address")
    subj = cmd["Additional_Params"]["Email_Subject"]
    subj = "" if is_none(subj) else subj
    if config.CONFIRM_BEFORE_SEND and not confirm(
            f"Send this email?\nTo: {to}\nSubject: {subj or '(none)'}\nMessage: {body}\nSay or tap yes to send."):
        raise Cancelled()
    url = (f"https://mail.google.com/mail/?view=cm&fs=1&to={quote(to)}"
           f"&su={quote(subj)}&body={quote(body)}")
    page = open_url(b, url)
    sel = ['div[role="textbox"][aria-label*="Message" i]']
    _first_visible(page, sel, 30000, "the Gmail compose box")
    page.keyboard.press("Control+Enter")
    end = time.time() + 6  # the compose window closes when the mail is sent
    while time.time() < end:
        try:
            if not page.locator(sel[0]).first.is_visible():
                return f"Sent your email to {to}."
        except Exception:
            return f"Sent your email to {to}."
        page.wait_for_timeout(500)
    return f"I pressed send for the email to {to}, but I could not confirm that it was sent. Please check Gmail."


# ---------------- Call ----------------
NO_CALL_BUTTON = "there is no call button"


def _call_buttons(platform, video):
    """Selectors of the call button. Language independent ones (icon names) come first; the role/name
    ones need the site to be in English."""
    word = "video" if video else "voice|audio"
    named = lambda p: p.get_by_role("button", name=re.compile(rf"({word}) call", re.I))
    if platform == "WhatsApp":
        label = "Video call" if video else "Voice call"
        icons = ['[data-icon*="video-call"]'] if video else ['[data-icon*="voice-call"]', '[data-icon*="audio-call"]']
        return [f'[aria-label="{label}"]', f'[title="{label}"]', f'button[aria-label*="{label}" i]'] + icons + [named]
    label = "Start a video call" if video else "Start a voice call"
    return [f'[aria-label="{label}"]', f'[aria-label*="{"video" if video else "voice"} call" i]', named]


def _call_via_menu(page, video) -> bool:
    """Some versions of WhatsApp Web have one 'Call' button that opens a small menu (voice / video)."""
    try:
        page.get_by_role("button", name=re.compile(r"^(start )?call$", re.I)).first.click(timeout=3000)
        item = re.compile("video" if video else "voice|audio", re.I)
        for role in ("menuitem", "button", "listitem"):
            entry = page.get_by_role(role, name=item).first
            if entry.is_visible():
                entry.click(timeout=3000)
                return True
    except Exception:
        pass
    return False


def _start_call(page, platform, video):
    """Click the call button of the open chat. Raises TaskFailed(NO_CALL_BUTTON...) when the site has none."""
    if platform not in ("WhatsApp", "Facebook"):  # Telegram web has no calls
        raise TaskFailed(f"{platform} web does not support calls")
    try:
        _first_visible(page, _call_buttons(platform, video), 6000, "the call button").click()
    except TaskFailed:
        if platform == "WhatsApp" and _call_via_menu(page, video):
            return
        raise TaskFailed(f"{NO_CALL_BUTTON} on {platform} web")


def _wait_for_answer(page) -> bool:
    """Best effort. True when the other person seems to have picked up, False when the call failed.
    If no ringing text can be seen (the site changed), it waits CALL_FALLBACK_WAIT seconds instead."""
    start, seen_ringing = time.time(), False
    while time.time() - start < config.CALL_ANSWER_TIMEOUT:
        try:
            text = page.inner_text("body")
        except Exception:
            text = ""
        if CALL_FAILED.search(text):
            return False
        if RINGING.search(text):
            seen_ringing = True
        elif seen_ringing:
            return True  # the ringing indicator disappeared: answered
        elif time.time() - start >= config.CALL_FALLBACK_WAIT:
            return True
        page.wait_for_timeout(1000)
    return False


def h_call(cmd, browser, confirm, ctx):
    p, who, message = cmd["Platform"], cmd["Target_Person"], cmd["Message_Body"]
    video = cmd["Additional_Params"]["Call_Type"] == "Video"
    lang = cmd.get("Reply_Language", "en")
    kind = "video" if video else "voice"
    if p not in ("WhatsApp", "Facebook"):
        raise TaskFailed(f"{p} web cannot make calls")
    page, title = _open_chat(p, browser, who, confirm)  # the right chat is open, nobody is called yet
    say_part = "" if is_none(message) else f"\nSay: {message}"
    if config.CONFIRM_BEFORE_SEND and not confirm(
            f"Start a {kind} call?\nApp: {p}\nPerson: {_to_line(title, who)}{say_part}\nSay or tap yes to call."):
        raise Cancelled()

    try:
        _start_call(page, p, video)
        answered = lambda: _wait_for_answer(page)
    except TaskFailed as e:
        if NO_CALL_BUTTON not in str(e) or p != "WhatsApp":
            raise
        phone = contact(who, "whatsapp")  # WhatsApp Web has no call button: use WhatsApp Desktop
        if not phone:
            raise TaskFailed("WhatsApp Web cannot call. Put the number in contacts.json to call with WhatsApp Desktop")
        desktop_call.start_whatsapp_call(phone, video)
        answered = lambda: (time.sleep(config.CALL_FALLBACK_WAIT) or True)

    if is_none(message) and not config.CALL_AUDIO_DEVICE:
        return f"Calling {title} on {p} ({kind} call)."
    if not answered():
        raise TaskFailed(f"{title} did not pick up the call")
    script = audio.build_call_script("" if is_none(message) else message, lang, localize)
    audio.speak(script, script_lang(script))
    return f"Called {title} on {p} and delivered your message." if not is_none(message) else f"Called {title} on {p}."


# ---------------- Schedule ----------------
def h_schedule(cmd, browser, confirm, ctx):
    when = datetime.strptime(cmd["Additional_Params"]["Time_or_Schedule"], "%Y-%m-%d %H:%M")
    end = when + timedelta(hours=1)
    fmt = "%Y%m%dT%H%M%S"
    url = ("https://calendar.google.com/calendar/render?action=TEMPLATE"
           f"&text={quote_plus(cmd['Message_Body'])}"
           f"&dates={when.strftime(fmt)}/{end.strftime(fmt)}&ctz={config.TIMEZONE}")
    page = open_url(browser, url)
    page.get_by_role("button", name=re.compile(r"^Save", re.I)).first.click(timeout=20000)
    return f"Added to your calendar: {cmd['Message_Body']}, {when:%d %b %I:%M %p}."


# ---------------- Fetch ----------------
def _short(items, n=5, width=110):
    items = [re.sub(r"\s+", " ", i).strip()[:width] for i in items if i.strip()]
    return items[:n]


def h_fetch(cmd, browser, confirm, ctx):
    p = cmd["Platform"]
    rng = cmd["Additional_Params"]["Time_or_Schedule"]
    today = datetime.now(ZoneInfo(config.TIMEZONE))

    if p == "Calendar":
        if rng == "Tomorrow":
            d = today + timedelta(days=1)
            url = f"https://calendar.google.com/calendar/u/0/r/day/{d.year}/{d.month}/{d.day}"
        elif rng == "This_Week":
            url = "https://calendar.google.com/calendar/u/0/r/week"
        else:
            url = "https://calendar.google.com/calendar/u/0/r/day"
        page = open_url(browser, url); page.wait_for_timeout(3500)
        events = _short(page.locator("[data-eventchip]").all_inner_texts(), n=8)
        return ("Schedule: " + "; ".join(events)) if events else "I found no events for that time."

    if p == "Gmail":
        page = open_url(browser, "https://mail.google.com/mail/u/0/#search/is%3Aunread")
        page.wait_for_selector("tr.zA", timeout=20000)
        rows = _short(page.locator("tr.zA").all_inner_texts())
        return ("Unread mail: " + "; ".join(rows)) if rows else "You have no unread mail."

    if p == "WhatsApp":
        page = open_url(browser, "https://web.whatsapp.com")
        _wait_ready(page, WA_SEARCH, WA_QR, "WhatsApp")
        rows = _short(page.locator('div[role="listitem"]:has(span[aria-label*="unread"])').all_inner_texts())
        return ("Unread chats: " + "; ".join(rows)) if rows else "You have no unread messages."

    if p == "Telegram":
        page = open_url(browser, "https://web.telegram.org/k/")
        _wait_ready(page, TG_SEARCH, TG_LOGIN, "Telegram")
        rows = _short(page.locator("a.chatlist-chat:has(.badge)").all_inner_texts())
        return ("Unread chats: " + "; ".join(rows)) if rows else "You have no unread messages."

    url = {"Facebook": "https://www.facebook.com/notifications",
           "LinkedIn": "https://www.linkedin.com/notifications/"}.get(p, cmd["URL"])
    page = open_url(browser, url); page.wait_for_timeout(2500)
    text = re.sub(r"\s+", " ", page.inner_text("body"))[:500]
    return text or "I found nothing."


# ---------------- Local skills ----------------
def h_skill(cmd, browser, confirm, ctx):
    return run_skill(cmd["Skill"], cmd["Skill_Args"], ctx)


DISPATCH = {"Open": h_open, "Search": h_search, "Play": h_play, "Message": h_message,
            "Call": h_call, "Schedule": h_schedule, "Fetch": h_fetch, "Skill": h_skill}


def execute(cmd: dict, browser, confirm, ctx=None) -> str:
    """Run one command. Returns a short English sentence, or raises TaskFailed / Cancelled / any error;
    the orchestrator turns those into the spoken reason."""
    fn = DISPATCH.get(cmd["Action"])
    if not fn:
        raise TaskFailed("this action is not supported yet")
    return fn(cmd, browser, confirm, ctx)