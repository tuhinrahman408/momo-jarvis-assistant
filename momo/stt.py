import time

from . import config, console


def prompt_text() -> str:
    return "\n" + console.paint("Your command > ", "prompt")


class TypedListener:
    def listen(self, prompt=None):
        try:
            return input(prompt_text() if prompt is None else prompt).strip() or None
        except EOFError:
            return None
        # Ctrl+C is not caught here, so it stops the program


class VoiceListener:
    def __init__(self):
        import speech_recognition as sr
        self.sr = sr
        self.rec = sr.Recognizer()
        self.rec.pause_threshold = 0.8
        self._calibrated_at = 0.0

    def _transcribe(self, audio):
        """Try every language in STT_LANGS and keep the most confident result."""
        sr = self.sr
        best_text, best_conf, errors = None, -1.0, 0
        for lang in config.STT_LANGS:
            try:
                res = self.rec.recognize_google(audio, language=lang, show_all=True)
            except sr.RequestError:
                errors += 1
                continue
            alts = res.get("alternative", []) if isinstance(res, dict) else []
            if not alts:
                continue
            conf = float(alts[0].get("confidence", 0.5))
            if conf > best_conf:
                best_text, best_conf = alts[0].get("transcript"), conf
        if best_text is None and errors == len(config.STT_LANGS):
            raise RuntimeError("the speech recognition service is not reachable, check the internet")
        return best_text

    def listen(self, prompt="", timeout=10):
        """Returns the recognised text, or None when nobody spoke. Raises when the microphone
        or the recognition service is not available (the caller reports it)."""
        sr = self.sr
        with sr.Microphone() as src:
            if time.time() - self._calibrated_at > 60:
                self.rec.adjust_for_ambient_noise(src, duration=0.4)
                self._calibrated_at = time.time()
            try:
                audio = self.rec.listen(src, timeout=timeout, phrase_time_limit=15)
            except sr.WaitTimeoutError:
                return None
        return self._transcribe(audio)
