"""Safe chat selection. Momo must never send a message or start a call to the wrong person, so:

1. rows of the search result are compared by the chat NAME (first line), not by the preview text of a message;
2. the search result must stop changing before anything is chosen (WhatsApp shows the old list first);
3. "Rakib" does not match "Rakibul"; several different matches -> the user is asked, nothing is guessed;
4. the row is read again right before the click, and the chat header is checked after the click.
"""
import time

from ..errors import Cancelled, TaskFailed, log_line
from ..util import name_score, norm

MAX_ROWS = 40
ASK_AT_MOST = 4

_SNAPSHOT_JS = "els => els.slice(0, %d).map(e => (e.innerText || '').trim())" % MAX_ROWS


def _lines(text: str) -> list:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def row_title(text: str) -> str:
    """The name of a chat row = its first line (the other lines are time, preview, username ...)."""
    lines = _lines(text)
    return lines[0] if lines else ""


def _snapshot(page, selectors):
    """(selector, [row texts]) of the first selector that currently finds rows."""
    for sel in selectors:
        try:
            texts = page.eval_on_selector_all(sel, _SNAPSHOT_JS)
        except Exception:
            continue
        if texts:
            return sel, texts
    return None, []


def _candidates(texts, who, title_lines):
    """[(score, index, title)] of the rows whose name fits `who`, one per distinct title."""
    seen, out = set(), []
    for i, text in enumerate(texts):
        lines = _lines(text)[:title_lines]
        if not lines:
            continue
        score = max(name_score(who, ln) for ln in lines)
        if not score:
            continue
        key = norm(lines[0])
        if key in seen:
            continue
        seen.add(key)
        out.append((score, i, lines[0]))
    return out


def pick_chat(page, row_selectors, who, confirm, what, timeout_ms=10000, title_lines=1):
    """Click the chat whose NAME is `who` and return its title. Raises TaskFailed when no chat has that name
    and Cancelled when the user rejects every candidate."""
    end = time.time() + timeout_ms / 1000
    last_titles, failed_clicks = None, 0
    while True:
        sel, texts = _snapshot(page, row_selectors)
        cands = _candidates(texts, who, title_lines)
        titles = [c[2] for c in cands]
        timed_out = time.time() >= end
        # Wait until the list has stopped changing (two equal snapshots), or the time is over.
        if cands and (titles == last_titles or timed_out):
            exact = [c for c in cands if c[0] == 3]
            if len(exact) == 1:
                chosen = exact[0]
            elif len(cands) == 1:
                chosen = cands[0]
            else:
                chosen = _ask(cands, who, confirm)
            title = _click_checked(page, sel, chosen)
            if title:
                return title
            failed_clicks += 1  # the list moved while we were choosing: look again, but not forever
            if failed_clicks >= 3:
                raise TaskFailed(f"the chat list of {what} kept changing, so I did not pick a chat")
        elif timed_out:
            raise TaskFailed(f"I could not find a chat named {who} on {what}")
        last_titles = titles if cands else None
        page.wait_for_timeout(450)


def _ask(cands, who, confirm):
    """Several different chats fit the name. Ask about them one by one; never pick one silently."""
    ordered = sorted(cands, key=lambda c: -c[0])[:ASK_AT_MOST]
    for _, idx, title in ordered:
        if confirm(f"I found {len(cands)} chats like '{who}'. Is it '{title}'? (y/n)"):
            return next(c for c in cands if c[1] == idx)
    raise Cancelled()


def _click_checked(page, sel, chosen):
    """Read the row again just before the click, so a list that changed in between cannot cause a wrong click."""
    _, idx, title = chosen
    try:
        loc = page.locator(sel).nth(idx)
        if norm(row_title(loc.inner_text())) != norm(title):
            return None
        loc.click()
        return title
    except (Cancelled, TaskFailed):
        raise
    except Exception:
        return None


def header_title(page, selectors) -> str:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible():
                text = row_title(loc.inner_text())
                if text:
                    return text
        except Exception:
            pass
    return ""


def verify_header(page, selectors, who, expected, confirm) -> str:
    """After the chat is open: if its header can be read it must fit the person. If it cannot be read, the
    name of the row that was clicked is trusted. Returns the title to show in the confirmation card."""
    shown = header_title(page, selectors)
    if not shown:
        return expected or who
    if name_score(who, shown) or (expected and norm(expected) == norm(shown)):
        return shown
    if confirm(f"The chat that opened is '{shown}', not '{who}'. Is that the right person? (y/n)"):
        return shown
    raise Cancelled()


def dump_labels(page, what: str) -> None:
    """Failure helper: write the visible button labels of the page to the log, so a changed website can be fixed
    quickly (send that part of ~/Momo/logs/momo_debug.log to whoever maintains the selectors)."""
    js = ("els => els.filter(e => e.offsetParent !== null).slice(0, 80).map(e => "
          "[e.tagName.toLowerCase(), e.getAttribute('aria-label') || '', e.getAttribute('title') || '', "
          "e.getAttribute('data-icon') || '', (e.innerText || '').trim().slice(0, 30)].join(' | '))")
    try:
        rows = page.eval_on_selector_all("[aria-label], [title], [data-icon], button, [role=button]", js)
        log_line(f"VISIBLE CONTROLS while looking for {what}:\n  " + "\n  ".join(dict.fromkeys(rows)))
    except Exception:
        pass