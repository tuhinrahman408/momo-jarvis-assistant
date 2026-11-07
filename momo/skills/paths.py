"""Path safety for file skills: only the user's home folder, never secrets or system folders."""
import os
from pathlib import Path

from .. import config

'''BLOCKED_PARTS = {".ssh", ".gnupg", "appdata", "browser_profile", ".env"}
'''
# Secrets and logged-in browser data: never readable, writable or deletable by a skill.
BLOCKED_PARTS = {".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", "appdata", "browser_profile",
                 "momoedge", ".env", "contacts.json", "ntuser.dat"}

def user_path(value, default_dir=None) -> Path:
    raw = Path(os.path.expanduser(str(value).strip().strip('"')))
    base = default_dir or config.WORKSPACE
    path = (raw if raw.is_absolute() else base / raw).resolve()
    home = Path.home().resolve()
    if home not in path.parents:
        raise ValueError("I only work inside your home folder.")
    # Only look at the part of the path below the home folder (the home path itself may contain
    # a name like AppData, for example a temp folder).
    if any(part.lower() in BLOCKED_PARTS for part in path.relative_to(home).parts):
        raise ValueError("That location is protected.")
    return path
