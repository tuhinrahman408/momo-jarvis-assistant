import argparse
import re

from momo import config, console
from momo.errors import log_exception, short_reason
from momo.parser import has_wake_word, strip_wake_word

# Say or type one of these words (alone) to stop Momo in text mode. "\u09ac\u09a8\u09cd\u09a7" is the
# Bengali word for "stop", written as an escape so this file stays English-only.
EXIT_WORDS = {"exit", "quit", "bye", "stop", "close momo", "\u09ac\u09a8\u09cd\u09a7"}
LOGIN_PAGES = ["https://web.whatsapp.com", "https://web.telegram.org/k/", "https://mail.google.com",
               "https://calendar.google.com", "https://facebook.com", "https://www.instagram.com",
               "https://linkedin.com"]


def run_terminal(voice: bool):
    """Text mode (coloured terminal). Nothing here is allowed to crash the program."""
    from momo.executor.browser import Browser
    from momo.orchestrator import Momo, GREETING, FAILED
    from momo.stt import TypedListener
    from momo import tts

    console.enable_ansi()
    browser = Browser()
    listener = TypedListener()
    if voice:
        try:
            from momo.stt import VoiceListener
            listener = VoiceListener()
        except Exception as e:
            tts.say(f"{FAILED} Reason: voice input is not available ({short_reason(e)}). Using the keyboard instead.", kind="error")
    momo = Momo(listener, browser)
    momo.start_services()
    tts.say(GREETING, kind="status")
    failures = 0
    try:
        while True:
            try:
                text = listener.listen()
                failures = 0
            except KeyboardInterrupt:
                raise
            except Exception as e:  # microphone or network problem: report it, never crash
                log_exception(e, "listen")
                failures += 1
                tts.say(f"{FAILED} Reason: {short_reason(e)}.", kind="error")
                if failures >= 3 and not isinstance(listener, TypedListener):
                    listener = momo.listener = TypedListener()
                    tts.say("Switching to the keyboard.", kind="status")
                continue
            if not text:
                continue
            if re.sub(r"[\s.!?,]+$", "", strip_wake_word(text).lower()) in EXIT_WORDS:
                print("Stopping Momo...")
                break
            if voice and config.REQUIRE_WAKE_WORD and not has_wake_word(text):
                print(console.paint("(ignored: please start with the word 'Momo')", "dim"))
                continue
            momo.process(text)
            console.divider()
    except KeyboardInterrupt:
        pass
    finally:
        momo.reminders.stop()
        browser.close()


def main():
    ap = argparse.ArgumentParser(description="Momo - voice/text personal AI assistant")
    ap.add_argument("--terminal", action="store_true", help="Use the text mode instead of the window")
    ap.add_argument("--voice", action="store_true", help="Text mode, but take commands from the microphone")
    ap.add_argument("--login", action="store_true", help="Open all sites once so you can log in")
    ap.add_argument("--once", metavar="TEXT", help="Run a single command and exit")
    ap.add_argument("--test-voice", action="store_true", help="Check which voice engines work")
    ap.add_argument("--dry", metavar="TEXT", help="Show the parsed JSON only, do not run anything")
    args = ap.parse_args()

    if args.test_voice:
        from momo.speech import selftest
        selftest()
        return

    if args.dry:
        import json
        from momo.parser import parse
        print(json.dumps({"MOMO": parse(args.dry)}, ensure_ascii=False, indent=2))
        return

    if args.login:
        from momo.executor.browser import Browser
        browser = Browser()
        for url in LOGIN_PAGES:
            browser.new_page().goto(url)
        input("Press Enter after finishing logins...")
        browser.close()
        return

    if args.once:
        from momo.executor.browser import Browser
        from momo.orchestrator import Momo
        from momo.stt import TypedListener
        browser = Browser()
        momo = Momo(TypedListener(), browser)
        momo.process(args.once)
        browser.close()
        return

    if args.terminal or args.voice:
        run_terminal(args.voice)
    else:
        from momo.gui import main as gui_main
        gui_main()


if __name__ == "__main__":
    main()
