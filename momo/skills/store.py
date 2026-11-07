"""Tiny JSON storage for to-dos, reminders, memory and history (kept in the workspace data folder)."""
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from .. import config


def _path(name: str):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return config.DATA_DIR / f"{name}.json"


def load(name: str, default):
    try:
        return json.loads(_path(name).read_text(encoding="utf-8"))
    except Exception:
        return default


def save(name: str, data) -> None:
    p = _path(name)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def now() -> datetime:
    return datetime.now(ZoneInfo(config.TIMEZONE))


def now_iso() -> str:
    return now().isoformat(timespec="minutes")
