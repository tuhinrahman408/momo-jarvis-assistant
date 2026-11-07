import re

from collections import deque

from . import config
from .errors import Cancelled, TaskFailed, log_dir, log_exception, log_line, short_reason
from .executor.handlers import execute
from .labels import task_label
from .lang import script_lang
from .parser import parse, chat, localize, generate_text
from .schema import SUCCESS, NEEDS, UNSUPPORTED
from .skills import memory as memory_store
from .skills.personal import ReminderService
from .skills.registry import Ctx
from .tts import say

# Fixed spoken phrases (always English). Change them here if you like.
GREETING = "Sir, your command."
LISTENING = "Sir, I am listening to you."
FAILED = "Sir, I could not complete your work."
CANCELLED = "Sir, okay, I cancelled it."


def working(label: str) -> str:
    return f"Sir, {label} working right now."


''' # Accepted "yes" answers. Bengali ones are unicode escapes, so this file stays English-only.
YES = ("y", "yes", "ok", "okay", "sure", "ha", "haan", "hya", "\u09b9", "\u09a0\u09bf\u0995")
'''
# Accepted answers. Bengali words are unicode escapes, so this file stays English-only.
# "yes" must be a clear, whole-word answer: "yesterday", "you ...", "hmm" or "yes but no" are NOT a yes.
YES = {"y", "yes", "yeah", "yep", "ok", "okay", "sure", "ha", "haan", "hya", "haa", "\u09b9", "\u099c\u09bf",
       "\u09b9\u09cd\u09af\u09be\u0981", "\u09b9\u09be\u0981", "\u09a0\u09bf\u0995"}
NO = {"n", "no", "nope", "nah", "na", "not", "dont", "don't", "cancel", "stop", "never", "wait",
      "\u09a8\u09be", "\u09a8\u09be\u09b9", "\u09a8\u09df", "\u09a8\u09bf\u09b7\u09c7\u09a7"}


def is_yes(answer) -> bool:
    words = re.findall(r"[\w\u0980-\u09ff']+", (answer or "").lower())
    if not words or len(words) > 4 or any(w in NO for w in words):
        return False
    return words[0] in YES

class Momo:
    def __init__(self, listener, browser, parse_fn=parse):
        self.listener = listener
        self.browser = browser
        self.parse = parse_fn
        self.history = []
        self.lang = "en"  # language of the current command; replies follow it
        self.on_state = lambda state: None  # window hook: "thinking" / "working" / "idle"
        self.reminders = ReminderService(self._notify)

    # ---------- output ----------
    def _say(self, text: str, translated: bool = False, kind: str = "momo"):
        """Fixed status lines and failure lines stay English. Results are translated only when the
        user's command was in Bengali or Banglish."""
        if kind == "momo" and not translated and self.lang != "en":
            text = localize(text, self.lang)
        say(text, self.lang, kind)

    def _fail(self, reason: str):
        self._say(f"{FAILED} Reason: {reason}.", kind="error")

    def _notify(self, text: str, lang: str):
        """Called by the reminder thread."""
        say(localize("Sir, reminder: " + text, lang), lang, "status")

    def start_services(self):
        if not self.reminders.is_alive():
            self.reminders.start()

    # ---------- helpers ----------
    def _ctx(self) -> Ctx:
        return Ctx(lang=self.lang, confirm=self._confirm, browser=self.browser,
                   llm=generate_text, say=self._say)

    def _confirm(self, question: str) -> bool:
        self._say(question, kind="confirm")
        '''ans = (self.listener.listen("(y/n) > ") or "").strip().lower()
        return ans.startswith(YES)'''
        return is_yes(self.listener.listen("(y/n) > "))

    def _ai_down(self, e: Exception):
        log_exception(e, "AI request")
        self._fail("I could not reach the AI service, please check the internet and the API key")

    def _chat(self, text: str):
        try:
            reply = chat(text, self.history, self.lang, memory_store.chat_context())
        except Exception as e:
            self._ai_down(e)
            return
        self.history = (self.history + [("user", text), ("model", reply)])[-12:]
        self._say(reply, translated=True)  # the chat model already answers in the user's language

    def _resolve(self, request: str):
        for _ in range(config.MAX_CLARIFY_ROUNDS + 1):
            try:
                cmd = self.parse(request)
            except Exception as e:
                self._ai_down(e)
                return None
            self.lang = cmd.get("Reply_Language", self.lang)
            if cmd["Status"] == SUCCESS:
                log_line(f"UNDERSTOOD: {cmd['Platform']} / {cmd['Action']} / person={cmd['Target_Person']} / "
                          f"message=<{len(cmd['Message_Body'])} chars> / skill={cmd['Skill']}")
                return cmd
            if cmd["Status"] == UNSUPPORTED and cmd["Platform"] == "None" and cmd["Action"] == "None":
                self._chat(request)
                return None
            self._say(cmd["Clarification_Prompt"], translated=cmd.get("Prompt_Source") == "llm")
            if cmd["Status"] != NEEDS:
                return None
            reply = self.listener.listen()
            if not reply:
                return None
            request = f"{request} {reply}"
        return None

    def _snapshot(self, cmd: dict):
        """Save a screenshot of the last web page, so a failed web task can be inspected later."""
        page = getattr(self.browser, "last_page", None)
        if page is None or cmd.get("Platform") in ("Local", "None"):
            return
        try:
            page.screenshot(path=str(log_dir() / "last_failure.png"))
        except Exception:
            pass

    def _run_task(self, cmd: dict) -> bool:
        """Say what is being done, do it, then report the result. Returns True on success."""
        self._say(working(task_label(cmd)), kind="status")
        self.on_state("working")
        try:
            result = execute(cmd, self.browser, self._confirm, self._ctx())
        except Cancelled:
            self._say(CANCELLED, kind="status")
            return False
        except Exception as e:
            log_exception(e, f"{cmd.get('Platform')} {cmd.get('Action')} {cmd.get('Skill')}")
            self._snapshot(cmd)
            self._fail(short_reason(e))
            return False
        finally:
            self.on_state("thinking")
        self._say(result)
        return True

    # ---------- main entry ----------
    def process(self, text: str):
        self.lang = script_lang(text)
        memory_store.log_command(text)
        self.on_state("thinking")
        try:
            queue, steps = deque([text]), 0
            while queue and steps < config.MAX_CHAINED_ACTIONS:
                steps += 1
                cmd = self._resolve(queue.popleft())
                if not cmd or not self._run_task(cmd):
                    break
                if cmd["Remaining_Request"] != "None":
                    queue.append(cmd["Remaining_Request"])
        except Exception as e:  # nothing may crash the program
            log_exception(e, "process")
            self._fail(short_reason(e))
        finally:
            self.on_state("idle")
