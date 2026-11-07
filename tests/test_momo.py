
import json
import re
from pathlib import Path

import pytest

from momo import config, audio
from momo.errors import Cancelled, TaskFailed, short_reason
from momo.lang import norm_lang, script_lang
from momo.parser import parse, strip_wake_word, has_wake_word
from momo.schema import SUCCESS, NEEDS
from momo.skills import SKILLS, Ctx, run_skill
from momo.skills.paths import user_path

NOW = "2026-11-02 09:00, Monday"
BN_MOMO = "\u09ae\u09ae\u09be"          # Bengali-script spelling of the wake word
BN_FACEBOOK = "\u09ab\u09c7\u09b8\u09ac\u09c1\u0995"


def fake(payload):
    return lambda system, user: payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)


def base(**kw):
    ap = kw.pop("ap", {})
    m = {"Status": "Success", "Clarification_Prompt": "None", "Platform": "None", "URL": "None",
         "Action": "None", "Target_Person": "None", "Message_Body": "None",
         "Additional_Params": ap, "Remaining_Request": "None"}
    m.update(kw)
    return {"MOMO": m}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "WORKSPACE", tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def ctx(**kw):
    return Ctx(confirm=kw.pop("confirm", lambda q: True), llm=kw.pop("llm", lambda s, u: ""), **kw)


# ---------------- parsing ----------------
def test_wake_word():
    assert strip_wake_word(f"{BN_MOMO}, {BN_FACEBOOK} open") == f"{BN_FACEBOOK} open"
    assert strip_wake_word("Momo open youtube") == "open youtube"
    assert has_wake_word("momo play a song")


def test_open_facebook():
    r = parse("momo facebook open", llm=fake(base(Platform="Facebook", Action="Open")), now=NOW)
    assert r["Status"] == SUCCESS and r["URL"] == "https://facebook.com"


def test_invalid_pair_downgraded():
    r = parse("youtube e call", llm=fake(base(Platform="YouTube", Action="Call", Target_Person="X")), now=NOW)
    assert r["Status"] == NEEDS and r["Prompt_Source"] == "system"


def test_youtube_play_url_rebuilt():
    r = parse("x", llm=fake(base(Platform="YouTube", Action="Play", URL="https://evil.com",
                                  ap={"Media_Title": "Coke Studio"})), now=NOW)
    assert r["URL"] == "https://www.youtube.com/results?search_query=Coke+Studio"


def test_schedule_needs_time():
    r = parse("x", llm=fake(base(Platform="Calendar", Action="Schedule", Message_Body="Meeting",
                                  ap={"Time_or_Schedule": "tomorrow"})), now=NOW)
    assert r["Status"] == NEEDS


def test_message_missing_person():
    r = parse("x", llm=fake(base(Platform="WhatsApp", Action="Message", Message_Body="hi")), now=NOW)
    assert r["Status"] == NEEDS


def test_fenced_json_and_garbage_retry():
    good = base(Platform="Web", Action="Search", ap={"Search_Query": "python"})
    r = parse("x", llm=fake("```json\n" + json.dumps(good) + "\n```"), now=NOW)
    assert r["URL"] == "https://www.google.com/search?q=python"
    r = parse("x", llm=fake("sorry I can't"), now=NOW)
    assert r["Status"] == NEEDS


def test_dangerous_url_blocked():
    r = parse("x", llm=fake(base(Platform="Web", Action="Open", URL="javascript:alert(1)")), now=NOW)
    assert r["Status"] == NEEDS


# ---------------- calls and messaging ----------------
def test_facebook_call_and_instagram_message_are_valid():
    r = parse("x", llm=fake(base(Platform="Facebook", Action="Call", Target_Person="Rakib",
                                  Message_Body="I am late")), now=NOW)
    assert r["Status"] == SUCCESS and r["Message_Body"] == "I am late"
    r = parse("x", llm=fake(base(Platform="Instagram", Action="Message", Target_Person="Rakib",
                                  Message_Body="hi")), now=NOW)
    assert r["Status"] == SUCCESS and r["URL"] == "https://www.instagram.com"


def test_call_needs_person():
    r = parse("x", llm=fake(base(Platform="WhatsApp", Action="Call")), now=NOW)
    assert r["Status"] == NEEDS


def test_call_script_starts_with_salam_and_keeps_message_verbatim():
    s = audio.build_call_script("I will be 10 minutes late", "en")
    assert s.startswith("Assalamu alaikum.")
    assert config.OWNER_NAME in s and s.endswith("I will be 10 minutes late")


def test_call_script_translates_only_the_frame():
    s = audio.build_call_script("ami late hobo", "banglish", lambda text, lang: "FRAME:")
    assert s == "FRAME: ami late hobo"


# ---------------- language ----------------
def test_language_detection():
    assert script_lang("open facebook") == "en"
    assert script_lang(f"{BN_FACEBOOK} open") == "bn"
    assert norm_lang("banglish", "facebook open koro") == "banglish"
    assert norm_lang("bn", "facebook open koro") == "banglish"      # model said bn but text is Latin
    assert norm_lang("en", f"{BN_FACEBOOK}") == "bn"                # model said en but text is Bengali
    assert norm_lang(None, "hello") == "en"


def test_reply_language_is_normalised():
    r = parse("facebook open koro", llm=fake(base(Platform="Facebook", Action="Open", Reply_Language="banglish")), now=NOW)
    assert r["Reply_Language"] == "banglish"


def test_source_code_has_no_bengali_text():
    root = Path(__file__).resolve().parent.parent
    files = list((root / "momo").rglob("*.py")) + [root / "run.py", root / "test_gemini.py"]
    for f in files:
        assert not re.search("[\u0980-\u09FF]", f.read_text(encoding="utf-8")), f


# ---------------- skills ----------------
def skill_cmd(name, **args):
    return base(Platform="Local", Action="Skill", Skill=name, Skill_Args=args)


def test_skill_validation():
    r = parse("x", llm=fake(skill_cmd("screenshot")), now=NOW)
    assert r["Status"] == SUCCESS and r["URL"] == "None"
    r = parse("x", llm=fake(skill_cmd("file_delete")), now=NOW)          # missing required path
    assert r["Status"] == NEEDS
    r = parse("x", llm=fake(skill_cmd("format_disk", drive="C")), now=NOW)  # unknown skill
    assert r["Status"] == NEEDS
    r = parse("x", llm=fake(skill_cmd("file_create", path="a.txt", evil="rm -rf")), now=NOW)
    assert r["Skill_Args"] == {"path": "a.txt"}                          # unknown args are dropped


def test_skill_name_alone_is_enough():
    r = parse("x", llm=fake(base(Skill="todo_list")), now=NOW)
    assert r["Status"] == SUCCESS and r["Platform"] == "Local" and r["Action"] == "Skill"


def test_risky_skill_needs_confirmation(workspace):
    f = workspace / "Momo" / "a.txt"
    f.parent.mkdir()
    f.write_text("x")
    with pytest.raises(Cancelled):
        run_skill("file_delete", {"path": str(f)}, ctx(confirm=lambda q: False))
    assert f.exists()


def test_path_safety(workspace):
    assert user_path("notes.txt") == (workspace / "notes.txt").resolve()
    for bad in ("/etc/passwd", "../../outside.txt", ".ssh/id_rsa"):
        with pytest.raises(ValueError):
            user_path(bad)


def test_file_create_and_rename(workspace):
    run_skill("file_create", {"path": "docs", "kind": "folder"}, ctx())
    run_skill("file_create", {"path": "docs/a.txt", "content": "hi"}, ctx())
    assert (workspace / "docs" / "a.txt").read_text() == "hi"
    run_skill("file_rename", {"path": "docs/a.txt", "new_name": "b.txt"}, ctx())
    assert (workspace / "docs" / "b.txt").exists()
    with pytest.raises(ValueError):
        run_skill("file_rename", {"path": "docs/b.txt", "new_name": "../c.txt"}, ctx())


def test_todo_and_report(workspace):
    c = ctx()
    run_skill("todo_add", {"task": "write report"}, c)
    run_skill("todo_add", {"task": "buy milk"}, c)
    assert "write report" in run_skill("todo_list", {}, c)
    run_skill("todo_done", {"task": "milk"}, c)
    assert "buy milk" not in run_skill("todo_list", {}, c)
    assert "1 tasks done" in run_skill("daily_report", {}, c)


def test_reminder_rules_and_service(workspace):
    c = ctx()
    with pytest.raises(ValueError):
        run_skill("reminder_set", {"text": "x", "when": "2020-01-01 10:00"}, c)
    with pytest.raises(ValueError):
        run_skill("reminder_set", {"text": "x", "when": "tomorrow"}, c)
    from momo.skills import personal, store
    run_skill("reminder_set", {"text": "call mom", "when": "2999-01-01 10:00"}, c)
    got = []
    svc = personal.ReminderService(lambda t, l: got.append(t))
    svc.check()
    assert got == []                                    # not due yet
    items = store.load("reminders", [])
    items[0]["when"] = "2020-01-01 10:00"
    store.save("reminders", items)
    svc.check(); svc.check()
    assert got == ["call mom"]                          # fired exactly once


def test_memory(workspace):
    c = ctx(llm=lambda s, u: "ok")
    run_skill("remember", {"text": "my exam is on Friday"}, c)
    assert "exam" in run_skill("recall", {"query": "exam"}, c)
    from momo.skills import memory
    assert "exam" in memory.chat_context()
    run_skill("project_note", {"name": "Portfolio", "note": "needs dark mode"}, c)
    assert "dark mode" in run_skill("project_info", {"name": "portfolio"}, c)
    with pytest.raises(Cancelled):
        run_skill("forget", {"text": "exam"}, ctx(confirm=lambda q: False))


def test_code_write_and_office(workspace):
    pytest.importorskip("docx"); pytest.importorskip("openpyxl")
    llm = lambda s, u: "```python\nprint('hi')\n```"
    out = run_skill("code_write", {"task": "print hi", "language": "python"}, ctx(llm=llm))
    assert "print('hi')" in next((workspace / "code").glob("*.py")).read_text()
    run_skill("doc_create", {"topic": "Test"}, ctx(llm=lambda s, u: "# Title\n\n- a\n- b"))
    assert list((workspace / "documents").glob("*.docx"))
    sheet = json.dumps({"headers": ["a", "b"], "rows": [[1, 2], ["=A2+B2", 3]]})
    run_skill("sheet_create", {"description": "x"}, ctx(llm=lambda s, u: sheet))
    assert list((workspace / "spreadsheets").glob("*.xlsx"))


def test_every_skill_is_documented():
    assert len(SKILLS) >= 40
    assert all(s.doc for s in SKILLS.values())


# ---------------- tabs ----------------
class FakePage:
    def __init__(self, url="about:blank"):
        self.url, self.closed = url, False

    def is_closed(self):
        return self.closed


class FakeCtx:
    def __init__(self, pages):
        self.pages = pages

    def new_page(self):
        p = FakePage()
        self.pages.append(p)
        return p


def fake_browser(pages):
    from momo.executor.browser import Browser
    b = Browser()
    b._ctx, b._pw = FakeCtx(pages), object()
    return b


def test_start_page_of_edge_is_reused_not_left_blank():
    first = FakePage("https://ntp.msn.com/edge/ntp?locale=en")
    b = fake_browser([first])
    assert b.page_for("https://www.youtube.com/results?search_query=a") is first
    assert len(b._ctx.pages) == 1                       # no extra tab was opened


def test_same_site_always_uses_the_same_tab():
    b = fake_browser([FakePage()])
    p1 = b.page_for("https://www.youtube.com/results?search_query=a")
    p1.url = "https://www.youtube.com/watch?v=1"
    assert b.page_for("https://www.youtube.com/results?search_query=b") is p1
    other = b.page_for("https://web.whatsapp.com")
    assert other is not p1 and len(b._ctx.pages) == 2
    other.url = "https://web.whatsapp.com/"
    assert b.page_for("https://web.whatsapp.com/send?phone=1") is other


def test_tab_from_an_earlier_run_is_adopted():
    old = FakePage("https://www.youtube.com/watch?v=9")
    b = fake_browser([FakePage("https://example.com"), old])
    assert b.page_for("https://youtube.com/results?search_query=x") is old
    assert len(b._ctx.pages) == 2


def test_closed_or_moved_tab_is_not_reused():
    b = fake_browser([FakePage()])
    p = b.page_for("https://www.youtube.com")
    p.url = "https://www.youtube.com/"
    p.closed = True
    assert b.page_for("https://www.youtube.com") is not p


# ---------------- outcome / spoken lines ----------------
from momo import orchestrator
from momo.labels import task_label
from momo.schema import normalize


class Said(list):
    def __call__(self, text, lang="en", kind="momo"):
        self.append((kind, text))


@pytest.fixture
def said(monkeypatch):
    out = Said()
    monkeypatch.setattr(orchestrator, "say", out)
    return out


class Typed:
    def __init__(self, *answers):
        self.answers = list(answers)

    def listen(self, prompt=""):
        return self.answers.pop(0) if self.answers else None


def make_momo(payload, listener=None):
    return orchestrator.Momo(listener or Typed(), None, parse_fn=lambda t: normalize(payload, t))


def test_task_says_working_then_result(workspace, said):
    m = make_momo(skill_cmd("todo_add", task="buy milk"))
    m.process("add todo buy milk")
    assert said[0] == ("status", "Sir, todo working right now.")
    assert said[1][0] == "momo" and "buy milk" in said[1][1]


def test_failed_task_says_short_reason(workspace, said, monkeypatch):
    def boom(*a, **k):
        raise TaskFailed("Rahim did not pick up the call")
    monkeypatch.setattr(orchestrator, "execute", boom)
    make_momo(base(Platform="WhatsApp", Action="Call", Target_Person="Rahim")).process("call Rahim on whatsapp")
    assert said[0] == ("status", "Sir, call working right now.")
    assert said[1] == ("error", "Sir, I could not complete your work. Reason: Rahim did not pick up the call.")


def test_unexpected_error_never_crashes(workspace, said, monkeypatch):
    monkeypatch.setattr(orchestrator, "execute", lambda *a, **k: 1 / 0)
    make_momo(skill_cmd("screenshot")).process("screenshot")
    assert said[-1][0] == "error" and said[-1][1].startswith("Sir, I could not complete your work.")


def test_cancel_is_not_an_error(workspace, said):
    m = make_momo(skill_cmd("file_delete", path="a.txt"), Typed("n"))
    m.process("delete a.txt")
    assert said[-1] == ("status", "Sir, okay, I cancelled it.")


def test_chat_reply_has_no_working_line(workspace, said, monkeypatch):
    payload = base(Status="Unsupported", Platform="None", Action="None", Clarification_Prompt="chat")
    monkeypatch.setattr(orchestrator, "chat", lambda *a, **k: "Paris is the capital of France.")
    make_momo(payload).process("capital of France?")
    assert said == [("momo", "Paris is the capital of France.")]


def test_task_labels():
    assert task_label({"Action": "Message"}) == "message"
    assert task_label({"Action": "Call"}) == "call"
    assert task_label({"Action": "Skill", "Skill": "todo_add"}) == "todo"
    assert task_label({"Action": "Skill", "Skill": "reminder_set"}) == "reminder"
    assert task_label({"Action": "Skill", "Skill": "open_browser"}) == "browser"


def test_short_reason():
    assert short_reason(TaskFailed("WhatsApp is not logged in.")) == "WhatsApp is not logged in"
    assert "layout" in short_reason(type("TimeoutError", (Exception,), {})("Timeout 30000ms exceeded.\nCall log: ..."))
    assert short_reason(ValueError("bad value\nsecond line")) == "bad value"


# ---------------- chrome profiles ----------------
def test_profile_matching():
    from momo.skills import browsers
    profiles = [("Default", ["Tuhin", "tuhin@gmail.com"]), ("Profile 1", ["Asif", "asif99@gmail.com"]),
                ("Profile 2", ["Daffodil", "DIU Student"])]
    assert browsers.find_profile(profiles, "asif") == "Profile 1"
    assert browsers.find_profile(profiles, "Daffodil") == "Profile 2"
    assert browsers.find_profile(profiles, "diu") == "Profile 2"
    assert browsers.find_profile(profiles, "tuhin@gmail.com") == "Default"
    with pytest.raises(TaskFailed) as e:
        browsers.find_profile(profiles, "nobody")
    assert "Asif" in str(e.value)                       # the reason lists the profiles that exist


def test_open_browser_uses_profile_folder(monkeypatch, tmp_path):
    from momo.skills import browsers
    state = tmp_path / "Local State"
    state.write_text(json.dumps({"profile": {"info_cache": {
        "Default": {"name": "Tuhin"}, "Profile 3": {"name": "Asif", "user_name": "asif@x.com"}}}}))
    monkeypatch.setattr(browsers, "_state_path", lambda info: state)
    monkeypatch.setattr(browsers, "_exe_path", lambda info: "chrome.exe")
    launched = []
    monkeypatch.setattr(browsers.subprocess, "Popen", lambda cmd: launched.append(cmd))
    out = run_skill("open_browser", {"profile": "asif", "url": "google.com"}, ctx())
    assert launched == [["chrome.exe", "--profile-directory=Profile 3", "https://google.com"]]
    assert "asif" in out


# ---------------- handlers / terminal / speech ----------------
def test_first_visible_gives_a_short_reason():
    from momo.executor.handlers import _first_visible

    class Loc:
        first = property(lambda self: self)
        def is_visible(self): return False

    class Page:
        def locator(self, sel): return Loc()
        def wait_for_timeout(self, ms): pass

    with pytest.raises(TaskFailed) as e:
        _first_visible(Page(), ["a", "b"], timeout_ms=50, what="the search box")
    assert "search box" in str(e.value)


def test_contacts_old_and_new_format(tmp_path, monkeypatch):
    from momo import util
    f = tmp_path / "contacts.json"
    f.write_text(json.dumps({"Rakib": {"email": "r@x.com", "whatsapp": "+8801700000000"}, "Rahim": "rahim@x.com"}))
    monkeypatch.setattr(config, "CONTACTS_FILE", f)
    assert util.contact("rakib", "whatsapp") == "+8801700000000"
    assert util.contact("Rahim", "email") == "rahim@x.com"
    assert util.contact("Rahim", "whatsapp") is None


def test_terminal_prompt_is_coloured():
    from momo import stt
    assert "Your command >" in stt.prompt_text() and "\033[" in stt.prompt_text()


def test_speech_text_is_cleaned():
    from momo.speech import _clean
    assert _clean("**Done**  #1\n\nfile") == "Done 1 file"


def test_window_modes_and_close(monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from momo.gui import Bridge, MomoWindow
    app = QApplication.instance() or QApplication([])

    class FakeAssistant:
        detached = False
        def __init__(self): self.posts = []
        def post(self, kind, value=None): self.posts.append((kind, value))

    bridge, a = Bridge(), FakeAssistant()
    w = MomoWindow(bridge, a)
    w.set_mode("keyboard")
    assert a.posts == []                                  # already selected: nothing is said again
    w.set_mode("voice")
    w.set_mode("voice")
    assert a.posts == [("mode", "voice")]                 # said once, when selected
    w.set_mode("keyboard")
    w.entry.setText("open facebook")
    w.send()
    assert a.posts[-1] == ("text", "open facebook")
    bridge.message.emit("error", "Sir, I could not complete your work.")
    bridge.state.emit("working"); bridge.speaking.emit(True)
    app.processEvents()
    w.close()
    assert a.detached and a.posts[-1][0] == "quit"


# ---------------- v3: wrong-person safety, calls, voice engines, confirm buttons ----------------
class Row:
    def __init__(self, text, log):
        self.text, self.log = text, log
        self.first = self

    def inner_text(self):
        return self.text

    def click(self):
        self.log.append(self.text.splitlines()[0])


class Rows:
    def __init__(self, texts, log):
        self.texts, self.log = texts, log
        self.first = Row(texts[0], log) if texts else None

    def filter(self, has_text):
        return Rows([t for t in self.texts if has_text.search(t)], self.log)

    def count(self):
        return len(self.texts)


class ListPage:
    def __init__(self, texts):
        self.log = []
        self.texts = texts

    def locator(self, sel):
        return Rows(self.texts, self.log)

    def wait_for_timeout(self, ms):
        pass


def test_chat_is_chosen_by_name_not_by_position():
    from momo.executor.handlers import _pick_row
    page = ListPage(["Karim Uddin\nhello", "Rakib Hasan\nok", "Sumon\nhi"])
    _pick_row(page, ["row"], "Rakib", lambda q: True, "WhatsApp")
    assert page.log == ["Rakib Hasan"]                    # not the first row


def test_nothing_is_sent_when_no_chat_matches():
    from momo.executor.handlers import _pick_row
    page = ListPage(["Karim Uddin\nhello", "Sumon\nhi"])
    with pytest.raises(TaskFailed) as e:
        _pick_row(page, ["row"], "Rakib", lambda q: True, "WhatsApp", timeout_ms=50)
    assert "Rakib" in str(e.value) and page.log == []


def test_several_matches_ask_the_user(monkeypatch):
    from momo.executor.handlers import _pick_row
    monkeypatch.setattr(config, "CONFIRM_BEFORE_SEND", True)
    page = ListPage(["Rakib Hasan\na", "Rakib Ahmed\nb"])
    asked = []
    with pytest.raises(Cancelled):
        _pick_row(page, ["row"], "Rakib", lambda q: asked.append(q) or False, "WhatsApp")
    assert asked and "2 chats" in asked[0] and page.log == []


def test_message_is_inserted_whole_not_typed_key_by_key():
    from momo.executor.handlers import _send_text
    events = []

    class Kb:
        def insert_text(self, t): events.append(("insert", t))
        def press(self, k): events.append(("press", k))

    class Pg:
        keyboard = Kb()
        def wait_for_timeout(self, ms): pass

    class Box:
        def click(self): events.append(("click",))

    _send_text(Pg(), Box(), "ami ashchhi \u0986\u09ae\u09bf")
    assert events == [("click",), ("insert", "ami ashchhi \u0986\u09ae\u09bf"), ("press", "Enter")]


def test_whatsapp_call_falls_back_to_desktop(monkeypatch):
    from momo.executor import handlers
    cmd = parse("x", llm=fake(base(Platform="WhatsApp", Action="Call", Target_Person="Rakib",
                                    ap={"Call_Type": "Voice"})), now=NOW)
    monkeypatch.setattr(config, "CONFIRM_BEFORE_SEND", False)
    monkeypatch.setattr(config, "CALL_AUDIO_DEVICE", "")
    monkeypatch.setitem(handlers.OPEN_CHAT, "WhatsApp", lambda b, who, c: object())

    def no_button(page, p, video):
        raise TaskFailed("there is no call button on WhatsApp web")
    monkeypatch.setattr(handlers, "_start_call", no_button)

    monkeypatch.setattr(handlers, "contact", lambda who, field: None)
    with pytest.raises(TaskFailed) as e:                  # no number: clear reason
        handlers.h_call(cmd, None, lambda q: True, None)
    assert "contacts.json" in str(e.value)

    started = []
    monkeypatch.setattr(handlers, "contact", lambda who, field: "+8801700000000")
    monkeypatch.setattr(handlers.desktop_call, "start_whatsapp_call", lambda phone, video: started.append(phone))
    assert "Calling Rakib" in handlers.h_call(cmd, None, lambda q: True, None)
    assert started == ["+8801700000000"]


def test_voice_engines_fall_back_and_report(monkeypatch):
    from momo import speech
    sp = speech.Speaker()
    monkeypatch.setattr(config, "VOICE_ENGINE", "auto")
    calls = []
    monkeypatch.setattr(sp, "_say_edge", lambda t, l: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(sp, "_say_sapi", lambda t, l: calls.append(("sapi", t)))
    sp.speak_now("hello", "en")
    assert calls == [("sapi", "hello")]                   # edge failed, the next engine spoke
    assert sp.order("en")[0] == "sapi"                    # the broken engine is skipped for a while
    assert sp.order("bn") == ["gtts"]                     # Bengali: edge is skipped, Google voice is next

    def broken(t, l): raise RuntimeError("no audio device")
    for name in ("sapi", "gtts"):
        monkeypatch.setattr(sp, "_say_" + name, broken)
    sp._bad.clear()
    monkeypatch.setattr(sp, "_say_edge", broken)
    with pytest.raises(RuntimeError) as e:
        sp.speak_now("hello", "en")
    assert "no voice engine worked" in str(e.value) and "edge" in str(e.value)


def test_confirmation_card_has_yes_no_buttons(monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from momo.gui import Bridge, MomoWindow
    app = QApplication.instance() or QApplication([])

    class A:
        detached = False
        def __init__(self): self.posts = []
        def post(self, kind, value=None): self.posts.append((kind, value))

    bridge, a = Bridge(), A()
    w = MomoWindow(bridge, a)
    bridge.message.emit("confirm", "Send this WhatsApp message?\nTo: Rakib\nMessage: hi")
    app.processEvents()
    assert w.confirm_row is not None and len(w.confirm_row.buttons) == 2
    w.confirm_row.buttons[0].click()                      # Yes
    assert a.posts[-1] == ("text", "yes") and w.confirm_row is None
    w.quick("open youtube")                               # quick command chip
    assert a.posts[-1] == ("text", "open youtube")
# ---------------- safe sending, yes/no, contacts ----------------
class Site:
    """A fake chat page: typing puts text in the box, Enter 'sends' it."""

    def __init__(self):
        self.box, self.body, self.sent = "", "", []
        self.keyboard = self

    def insert_text(self, t): self.box = t
    def press(self, k):
        if k == "Enter":
            self.sent.append(self.box); self.body += " " + self.box; self.box = ""
    def locator(self, sel): return self
    first = property(lambda self: self)
    def is_visible(self): return True
    def click(self): pass
    def inner_text(self, sel=None): return self.box if sel is None else self.body
    def wait_for_timeout(self, ms): pass


def _msg_cmd(who="Rakib", body="ami ashchhi"):
    return {"Platform": "WhatsApp", "Target_Person": who, "Message_Body": body,
            "Additional_Params": {"Email_Subject": "None"}}


def test_confirmation_card_shows_the_real_chat_name(monkeypatch):
    from momo.executor import handlers
    site, asked = Site(), []
    monkeypatch.setattr(config, "CONFIRM_BEFORE_SEND", True)
    monkeypatch.setitem(handlers.OPEN_CHAT, "WhatsApp", lambda b, who, c: (site, "Rakib Hasan"))
    out = handlers.h_message(_msg_cmd(), None, lambda q: asked.append(q) or True, None)
    assert "To: Rakib Hasan" in asked[0] and "ami ashchhi" in asked[0]
    assert site.sent == ["ami ashchhi"] and out == "Sent your message to Rakib Hasan on WhatsApp."


def test_nothing_is_typed_when_the_user_says_no(monkeypatch):
    from momo.executor import handlers
    site = Site()
    monkeypatch.setattr(config, "CONFIRM_BEFORE_SEND", True)
    monkeypatch.setitem(handlers.OPEN_CHAT, "WhatsApp", lambda b, who, c: (site, "Rakib Hasan"))
    with pytest.raises(Cancelled):
        handlers.h_message(_msg_cmd(), None, lambda q: False, None)
    assert site.sent == [] and site.box == ""


def test_card_tells_when_the_found_name_differs(monkeypatch):
    from momo.executor import handlers
    asked = []
    monkeypatch.setattr(config, "CONFIRM_BEFORE_SEND", True)
    monkeypatch.setitem(handlers.OPEN_CHAT, "WhatsApp", lambda b, who, c: (Site(), "Sumon Mia"))
    handlers.h_message(_msg_cmd(), None, lambda q: asked.append(q) or True, None)
    assert "Sumon Mia (you said 'Rakib')" in asked[0]


def test_header_of_the_opened_chat_is_checked(monkeypatch):
    from momo.executor import chats

    class Hdr:
        first = property(lambda self: self)
        def __init__(self, text): self.text = text
        def locator(self, sel): return self
        def is_visible(self): return True
        def inner_text(self): return self.text

    assert chats.verify_header(Hdr("Rakib Hasan"), ["h"], "Rakib", "Rakib Hasan", lambda q: False) == "Rakib Hasan"
    asked = []
    with pytest.raises(Cancelled):
        chats.verify_header(Hdr("Karim"), ["h"], "Rakib", "Rakib Hasan", lambda q: asked.append(q) or False)
    assert "Karim" in asked[0]
    assert chats.verify_header(Hdr(""), ["h"], "Rakib", "Rakib Hasan", lambda q: False) == "Rakib Hasan"


def test_email_must_be_one_valid_address(monkeypatch):
    from momo.executor import handlers
    monkeypatch.setattr(config, "CONFIRM_BEFORE_SEND", False)
    with pytest.raises(TaskFailed):
        handlers._msg_gmail(_msg_cmd("a@x.com,b@y.com"), None, lambda q: True, "a@x.com,b@y.com", "hi")


def test_yes_must_be_a_clear_yes():
    from momo.orchestrator import is_yes
    for ok in ("yes", "Yes.", "y", "okay go ahead", "ha", "\u09b9\u09cd\u09af\u09be\u0981", "yes please"):
        assert is_yes(ok), ok
    for bad in ("", None, "no", "yesterday", "you idiot", "yes but no", "hmm", "\u09a8\u09be",
                "yes i think so but maybe not now"):
        assert not is_yes(bad), bad


def test_contacts_by_alias_and_no_guessing(tmp_path, monkeypatch):
    from momo import util
    f = tmp_path / "contacts.json"
    f.write_text(json.dumps({"Rakib": {"whatsapp": "01712345678", "aliases": ["Rakib Hasan"]},
                             "Sumon Mia": {"whatsapp": "01800000000"}, "Sumon Das": {"whatsapp": "01900000000"}}))
    monkeypatch.setattr(config, "CONTACTS_FILE", f)
    assert util.contact_key("Rakib vai") == "Rakib"
    assert util.contact("rakib hasan", "whatsapp") == "01712345678"
    assert util.find_contact("Sumon") == (None, None)
    assert util.contact("Sumon Das", "whatsapp") == "01900000000"
    assert util.wa_digits("01712-345678") == "8801712345678" and util.wa_digits("+880 1712 345678") == "8801712345678"


def test_dangerous_key_combinations_are_blocked_in_any_order():
    from momo.skills.system import BLOCKED_KEYS
    assert frozenset("r+win".split("+")) in BLOCKED_KEYS and frozenset("alt+f4".split("+")) in BLOCKED_KEYS


def test_browser_data_and_contacts_are_protected(workspace):
    for bad in ("MomoEdge/Default/Cookies", "contacts.json", ".aws/credentials"):
        with pytest.raises(ValueError):
            user_path(bad)

