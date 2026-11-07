"""Speech synthesis and playback. Neural voices come from Microsoft Edge's online voices (edge-tts);
Google text-to-speech is the fallback.

For calls: the text is played on the audio device set in CALL_AUDIO_DEVICE. Use a virtual audio cable
(for example VB-Cable): play on "CABLE Input" and select "CABLE Output" as the microphone of the call
page, so the other person hears Momo. With no device set, the speech plays on the default speakers."""
import asyncio
import io

from . import config
from .lang import script_lang, tts_lang

SAMPLE_RATE = 44100


def build_call_script(message: str, lang: str = "en", localize_fn=None) -> str:
    """Spoken text of a call: salam, who I am, then exactly what the user wanted said."""
    owner = config.OWNER_NAME
    if message and message != "None":
        frame = f"Assalamu alaikum. I am the assistant made by {owner}. {owner} asked me to let you know:"
        if localize_fn and lang != "en":
            frame = localize_fn(frame, lang)
        return f"{frame} {message}"
    frame = f"Assalamu alaikum. I am the assistant made by {owner}. {owner} asked me to call you."
    return localize_fn(frame, lang) if localize_fn and lang != "en" else frame


def _find_device(name: str):
    import sounddevice as sd
    if not name:
        return None
    for i, d in enumerate(sd.query_devices()):
        if name.lower() in d["name"].lower() and d["max_output_channels"] > 0:
            return i
    raise RuntimeError(f"Audio output device '{name}' was not found. Check CALL_AUDIO_DEVICE in .env.")


def synth_edge(text: str, lang: str) -> bytes:
    import edge_tts
    voice = config.VOICE_BN if lang == "bn" else config.VOICE_EN

    async def fetch() -> bytes:
        data = b""
        async for chunk in edge_tts.Communicate(text, voice).stream():
            if chunk["type"] == "audio":
                data += chunk["data"]
        return data

    data = asyncio.run(asyncio.wait_for(fetch(), 20))
    if not data:
        raise RuntimeError("the online voice returned no audio")
    return data


def synth_gtts(text: str, lang: str) -> bytes:
    from gtts import gTTS
    buf = io.BytesIO()
    gTTS(text=text, lang=tts_lang(lang)).write_to_fp(buf)
    return buf.getvalue()


def synthesize(text: str, lang: str) -> bytes:
    try:
        return synth_edge(text, lang)
    except Exception:
        return synth_gtts(text, lang)


def play_mp3(data: bytes, device=None) -> None:
    """Blocking: decode MP3 bytes and play them (default speakers unless a device index is given)."""
    import miniaudio
    import numpy as np
    import sounddevice as sd
    decoded = miniaudio.decode(data, output_format=miniaudio.SampleFormat.SIGNED16,
                               nchannels=1, sample_rate=SAMPLE_RATE)
    sd.play(np.frombuffer(decoded.samples, dtype=np.int16), SAMPLE_RATE, device=device)
    sd.wait()


def speak(text: str, lang: str = None) -> None:
    """Speak into a call: plays on CALL_AUDIO_DEVICE. Blocking."""
    lang = lang or script_lang(text)
    play_mp3(synthesize(text, lang), _find_device(config.CALL_AUDIO_DEVICE))
