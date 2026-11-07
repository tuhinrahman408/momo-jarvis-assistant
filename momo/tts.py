"""Every reply goes through say(): it is shown (window or terminal) and spoken."""
from . import console
from .speech import speaker

_SINK = None


def set_sink(fn):
    """The window attaches itself here: fn(kind, text) with kind = momo / status / error / user."""
    global _SINK
    _SINK = fn


def say(text: str, lang: str = "en", kind: str = "momo") -> None:
    if _SINK:
        _SINK(kind, text)
    else:
        console.show(text, kind)
    speaker.say(text, lang)
