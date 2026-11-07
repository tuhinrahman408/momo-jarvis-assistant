"""Spoken replies. Sentences are queued and spoken one after another in a background thread, so Momo
never blocks while talking. Several voice engines are tried in turn, so one broken engine does not
silence Momo:
  edge  = Microsoft neural voices (online, best quality, English and Bengali)
  sapi  = the Windows built-in voice (offline, English)
  gtts  = Google text-to-speech (online)
If every engine fails, the reason is shown in the window (once) and written to the log file."""
import queue
import re
import subprocess
import sys
import threading
import time

from . import audio, config
from .errors import log_exception

ENGINES_EN = ("edge", "sapi", "gtts")
ENGINES_BN = ("edge", "gtts")


def _clean(text: str) -> str:
    text = re.sub(r"[*_#`>]", "", text)
    return re.sub(r"\s+", " ", text).strip()[:350]


class Speaker:
    def __init__(self):
        self._q = queue.Queue()
        self._thread = None
        self._bad = {}                 # engine -> time of its last failure
        self._last_problem = ""
        self.callbacks = []            # fn(True/False): speaking started / stopped
        self.problem_callbacks = []    # fn(text): no engine worked

    # ---------- public ----------
    def say(self, text: str, lang: str = "en") -> None:
        text = _clean(text)
        if not text or not config.VOICE_REPLY:
            return
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="momo-speech", daemon=True)
            self._thread.start()
        self._q.put((text, lang))

    def wait(self, timeout: float = None) -> None:
        """Block until everything queued has been spoken."""
        end = time.time() + timeout if timeout else None
        while self._q.unfinished_tasks and (end is None or time.time() < end):
            time.sleep(0.05)

    # ---------- engines ----------
    def order(self, lang: str) -> list:
        base = list(ENGINES_BN if lang == "bn" else ENGINES_EN)
        pref = config.VOICE_ENGINE
        if pref in base:
            base.remove(pref)
            base.insert(0, pref)
        now = time.time()
        fresh = [e for e in base if now - self._bad.get(e, 0) > 120]  # skip engines that just failed
        return fresh or base

    def _say_edge(self, text, lang):
        audio.play_mp3(audio.synth_edge(text, lang))

    def _say_gtts(self, text, lang):
        audio.play_mp3(audio.synth_gtts(text, lang))

    def _say_sapi(self, text, lang):
        if not sys.platform.startswith("win"):
            raise RuntimeError("the Windows voice is not available on this system")
        script = ("Add-Type -AssemblyName System.Speech; "
                  "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  "$s.Speak([Console]::In.ReadToEnd())")
        subprocess.run(["powershell", "-NoProfile", "-Command", script], input=text, text=True,
                       encoding="utf-8", timeout=60, check=True, creationflags=0x08000000)  # no console window

    def speak_now(self, text: str, lang: str = "en") -> None:
        """Speak one sentence (blocking), trying the engines in order. Raises if none works."""
        errors = []
        for name in self.order(lang):
            try:
                getattr(self, "_say_" + name)(text, lang)
                self._bad.pop(name, None)
                return
            except Exception as e:
                self._bad[name] = time.time()
                errors.append(f"{name}: {type(e).__name__}: {str(e).splitlines()[0][:70] if str(e) else ''}")
                log_exception(e, f"voice engine {name}")
        raise RuntimeError("no voice engine worked (" + "; ".join(errors) + ")")

    # ---------- worker ----------
    def _notify(self, speaking: bool) -> None:
        for fn in list(self.callbacks):
            try:
                fn(speaking)
            except Exception:
                pass

    def _problem(self, message: str) -> None:
        if message == self._last_problem:
            return
        self._last_problem = message
        for fn in list(self.problem_callbacks):
            try:
                fn(message)
            except Exception:
                pass
        if not self.problem_callbacks:
            print(f"(voice reply problem: {message})")

    def _run(self) -> None:
        while True:
            text, lang = self._q.get()
            try:
                self._notify(True)
                self.speak_now(text, lang)
                self._last_problem = ""
            except Exception as e:
                self._problem(str(e))
            finally:
                self._q.task_done()
                if self._q.empty():
                    self._notify(False)


speaker = Speaker()


def selftest() -> None:
    """python run.py --test-voice : tries every engine once and says which one works."""
    for name in ("edge", "sapi", "gtts"):
        try:
            getattr(speaker, "_say_" + name)("Sir, this is a voice test.", "en")
            print(f"{name:5} OK")
        except Exception as e:
            print(f"{name:5} FAILED  {type(e).__name__}: {str(e)[:120]}")
