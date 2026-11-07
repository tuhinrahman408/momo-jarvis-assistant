"""WhatsApp Web has no call button in most accounts, so calls go through the WhatsApp Desktop app.
Windows only. Needs `pip install pywinauto`, WhatsApp Desktop installed and logged in, and the
person's number in contacts.json ("whatsapp": "+880...")."""
import os
import sys
import time

from ..errors import TaskFailed
from ..util import wa_digits


def start_whatsapp_call(phone: str, video: bool = False) -> None:
    if not sys.platform.startswith("win"):
        raise TaskFailed("WhatsApp Desktop calls work only on Windows")
    try:
        from pywinauto import Desktop
    except ImportError:
        raise TaskFailed("the package pywinauto is missing (pip install pywinauto)")
    digits = wa_digits(phone)
    if not digits:
        raise TaskFailed("the WhatsApp number in contacts.json is empty")
    try:
        os.startfile(f"whatsapp://send?phone={digits}")  # opens the chat in WhatsApp Desktop
    except OSError:
        raise TaskFailed("WhatsApp Desktop is not installed")
    label = "Video call" if video else "Voice call"
    end = time.time() + 25
    while time.time() < end:
        try:
            window = Desktop(backend="uia").window(title_re=".*WhatsApp.*")
            button = window.child_window(title_re=f"(?i).*{label}.*", control_type="Button")
            if button.exists(timeout=1):
                button.click_input()
                return
        except Exception:
            pass
        time.sleep(1)
    raise TaskFailed("I could not find the call button in WhatsApp Desktop")