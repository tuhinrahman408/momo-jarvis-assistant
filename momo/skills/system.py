"""Computer control: power, apps, files, screenshot, volume, brightness, keyboard and mouse.
Heavy libraries are imported inside the functions so the rest of Momo works without them."""
import json
import subprocess
import sys
from datetime import datetime

from .. import config
from .paths import user_path
from .registry import skill

IS_WIN = sys.platform.startswith("win")

APPS = {
    "notepad": "notepad.exe", "calculator": "calc.exe", "paint": "mspaint.exe",
    "file explorer": "explorer.exe", "cmd": "cmd.exe", "powershell": "powershell.exe",
    "task manager": "taskmgr.exe", "edge": "msedge.exe", "chrome": "chrome.exe",
    "firefox": "firefox.exe", "vscode": "code", "word": "winword.exe", "excel": "excel.exe",
    "powerpoint": "powerpnt.exe", "settings": "ms-settings:",
}
ALIASES = {"calc": "calculator", "vs code": "vscode", "visual studio code": "vscode", "code": "vscode",
           "microsoft edge": "edge", "google chrome": "chrome", "explorer": "file explorer",
           "files": "file explorer", "command prompt": "cmd", "terminal": "cmd"}
NEVER_CLOSE = {"file explorer", "settings"}
'''BLOCKED_KEYS = {"alt+f4", "ctrl+alt+delete"}'''
BLOCKED_KEYS = {frozenset(k.split("+")) for k in (
    "alt+f4", "ctrl+alt+delete", "ctrl+alt+del", "win+r", "win+x", "ctrl+shift+esc")}


def _apps() -> dict:
    apps = dict(APPS)
    try:
        apps.update({k.lower(): v for k, v in json.loads(config.APPS_FILE.read_text(encoding="utf-8")).items()})
    except Exception:
        pass
    return apps


def _app(name: str):
    key = ALIASES.get(name.strip().lower(), name.strip().lower())
    apps = _apps()
    if key not in apps:
        raise ValueError(f"I don't know the app '{name}'. Add it to apps.json.")
    return key, apps[key]


@skill("open_app", required=("name",), doc="Open an installed application by name")
def open_app(args, ctx):
    key, target = _app(args["name"])
    if IS_WIN:
        subprocess.Popen(["cmd", "/c", "start", "", target])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-a", target])
    else:
        subprocess.Popen([target])
    return f"Opened {key}."


@skill("close_app", required=("name",), risky=True, doc="Close a running application by name (unsaved work may be lost)")
def close_app(args, ctx):
    import psutil
    key, target = _app(args["name"])
    if key in NEVER_CLOSE:
        raise ValueError(f"I won't close {key}.")
    exe = target.lower().rsplit("\\", 1)[-1]
    count = 0
    for p in psutil.process_iter(["name"]):
        pname = (p.info["name"] or "").lower()
        if pname == exe or pname == exe.removesuffix(".exe"):
            try:
                p.terminate()
                count += 1
            except psutil.Error:
                pass
    return f"Closed {key}." if count else f"{key} is not running."


@skill("lock_pc", doc="Lock the computer screen")
def lock_pc(args, ctx):
    if IS_WIN:
        subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"], check=False)
    elif sys.platform == "darwin":
        subprocess.run(["pmset", "displaysleepnow"], check=False)
    else:
        subprocess.run(["loginctl", "lock-session"], check=False)
    return "Locked the PC."


@skill("shutdown_pc", risky=True, doc="Shut down the computer in 30 seconds")
def shutdown_pc(args, ctx):
    subprocess.run(["shutdown", "/s", "/t", "30"] if IS_WIN else ["shutdown", "-h", "+1"], check=True)
    return "The PC will shut down soon. Say 'cancel shutdown' to stop it."


@skill("restart_pc", risky=True, doc="Restart the computer in 30 seconds")
def restart_pc(args, ctx):
    subprocess.run(["shutdown", "/r", "/t", "30"] if IS_WIN else ["shutdown", "-r", "+1"], check=True)
    return "The PC will restart soon. Say 'cancel shutdown' to stop it."


@skill("cancel_shutdown", doc="Cancel a pending shutdown or restart")
def cancel_shutdown(args, ctx):
    subprocess.run(["shutdown", "/a"] if IS_WIN else ["shutdown", "-c"], check=False)
    return "Cancelled the pending shutdown or restart."


@skill("file_create", required=("path",), optional=("kind", "content"),
       doc="Create a file or folder (kind = file or folder) inside the home folder")
def file_create(args, ctx):
    p = user_path(args["path"])
    kind = args.get("kind", "").lower() or ("file" if p.suffix else "folder")
    if kind == "folder":
        p.mkdir(parents=True, exist_ok=True)
    else:
        if p.exists():
            raise FileExistsError(f"{p.name} already exists.")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args.get("content", ""), encoding="utf-8")
    return f"Created {kind}: {p}"


@skill("file_rename", required=("path", "new_name"), doc="Rename a file or folder")
def file_rename(args, ctx):
    p = user_path(args["path"])
    new = args["new_name"].strip()
    if not p.exists():
        raise FileNotFoundError(f"{p} does not exist.")
    if new != new.replace("/", "").replace("\\", ""):
        raise ValueError("The new name must not contain slashes.")
    target = p.with_name(new)
    if target.exists():
        raise FileExistsError(f"{new} already exists.")
    p.rename(target)
    return f"Renamed {p.name} to {new}."


@skill("file_delete", required=("path",), risky=True, doc="Move a file or folder to the recycle bin")
def file_delete(args, ctx):
    from send2trash import send2trash
    p = user_path(args["path"])
    if not p.exists():
        raise FileNotFoundError(f"{p} does not exist.")
    send2trash(str(p))
    return f"Moved {p.name} to the recycle bin."


@skill("screenshot", doc="Take a screenshot and save it")
def screenshot(args, ctx):
    import pyautogui
    out = config.WORKSPACE / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"screenshot_{datetime.now():%Y%m%d_%H%M%S}.png"
    pyautogui.screenshot().save(path)
    return f"Screenshot saved: {path}"


@skill("volume", optional=("level", "action"), doc="Set the volume (level 0-100) or change it (action = up, down, mute)")
def volume(args, ctx):
    import pyautogui
    if "level" in args:
        level = max(0, min(100, int(float(args["level"]))))
        pyautogui.press("volumedown", presses=50, interval=0.01)
        pyautogui.press("volumeup", presses=level // 2, interval=0.01)
        return f"Volume set to about {level}%."
    action = args.get("action", "").lower()
    if action in ("up", "down"):
        pyautogui.press(f"volume{action}", presses=5, interval=0.01)
        return f"Volume {action}."
    if action in ("mute", "unmute"):
        pyautogui.press("volumemute")
        return "Toggled mute."
    raise ValueError("Tell me a level from 0 to 100, or up, down or mute.")


@skill("brightness", optional=("level", "action"), doc="Set the screen brightness (level 0-100) or change it (action = up, down)")
def brightness(args, ctx):
    import screen_brightness_control as sbc
    if "level" in args:
        level = max(0, min(100, int(float(args["level"]))))
    else:
        current = sbc.get_brightness()[0]
        step = {"up": 10, "down": -10}.get(args.get("action", "").lower())
        if step is None:
            raise ValueError("Tell me a level from 0 to 100, or up or down.")
        level = max(0, min(100, current + step))
    sbc.set_brightness(level)
    return f"Brightness set to {level}%."


@skill("type_text", required=("text",), risky=True, doc="Type text with the keyboard into the window that is currently focused")
def type_text(args, ctx):
    import pyautogui
    text = args["text"]
    if text.isascii():
        pyautogui.write(text, interval=0.02)
    else:  # non-ASCII text is pasted through the clipboard
        import pyperclip
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
    return "Typed the text."


@skill("press_keys", required=("keys",), doc="Press a keyboard shortcut such as ctrl+c or enter")
def press_keys(args, ctx):
    import pyautogui
    combo = args["keys"].lower().replace(" ", "")
    '''if combo in BLOCKED_KEYS:'''
    if frozenset(combo.split("+")) in BLOCKED_KEYS:
        raise ValueError("I won't press that combination. Use the close app skill instead.")
    pyautogui.hotkey(*combo.split("+"))
    return f"Pressed {combo}."


@skill("mouse", required=("action",), optional=("x", "y"),
       doc="Control the mouse (action = click, double, right, move) at screen position x, y or at the current position")
def mouse(args, ctx):
    import pyautogui
    action = args["action"].lower()
    if action not in ("click", "double", "right", "move"):
        raise ValueError("Mouse action must be click, double, right or move.")
    pos = {}
    if "x" in args and "y" in args:
        w, h = pyautogui.size()
        x, y = int(float(args["x"])), int(float(args["y"]))
        if not (0 <= x < w and 0 <= y < h):
            raise ValueError("That position is outside the screen.")
        pos = {"x": x, "y": y}
    if action == "move":
        if not pos:
            raise ValueError("Give me x and y to move the mouse.")
        pyautogui.moveTo(**pos, duration=0.3)
    elif action == "double":
        pyautogui.doubleClick(**pos)
    elif action == "right":
        pyautogui.rightClick(**pos)
    else:
        pyautogui.click(**pos)
    return f"Mouse {action} done."
