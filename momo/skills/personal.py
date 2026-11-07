"""Personal management: to-do list, reminders, daily routine, daily report."""
import threading
from datetime import datetime

from . import store
from .registry import skill

TIME_FMT = "%Y-%m-%d %H:%M"
_LOCK = threading.RLock()


def _local_now() -> datetime:
    return store.now().replace(tzinfo=None)


# ---------------- To-do list ----------------
@skill("todo_add", required=("task",), doc="Add a task to the to-do list")
def todo_add(args, ctx):
    with _LOCK:
        todos = store.load("todos", [])
        nid = max([t["id"] for t in todos], default=0) + 1
        todos.append({"id": nid, "task": args["task"], "done": False,
                      "created": store.now_iso(), "done_at": None})
        store.save("todos", todos)
    return f"Added task {nid}: {args['task']}"


@skill("todo_list", doc="Show the pending tasks")
def todo_list(args, ctx):
    pending = [t for t in store.load("todos", []) if not t["done"]]
    if not pending:
        return "Your to-do list is empty."
    return "Pending tasks: " + "; ".join(f"{t['id']}) {t['task']}" for t in pending)


@skill("todo_done", required=("task",), doc="Mark a task as done (by its number or part of its text)")
def todo_done(args, ctx):
    key = args["task"].strip().lower()
    with _LOCK:
        todos = store.load("todos", [])
        for t in todos:
            if not t["done"] and (key == str(t["id"]) or key in t["task"].lower()):
                t["done"], t["done_at"] = True, store.now_iso()
                store.save("todos", todos)
                return f"Marked done: {t['task']}"
    return "I could not find that task."


# ---------------- Reminders ----------------
@skill("reminder_set", required=("text", "when"), doc="Set a reminder; when = 'YYYY-MM-DD HH:MM' in local time")
def reminder_set(args, ctx):
    try:
        when = datetime.strptime(args["when"].strip(), TIME_FMT)
    except ValueError:
        raise ValueError("I need the reminder time as date and time.")
    if when <= _local_now():
        raise ValueError("That time has already passed.")
    with _LOCK:
        items = store.load("reminders", [])
        items.append({"id": max([i["id"] for i in items], default=0) + 1, "text": args["text"],
                      "when": when.strftime(TIME_FMT), "lang": ctx.lang, "fired": False})
        store.save("reminders", items)
    return f"Reminder set for {when:%d %b, %I:%M %p}: {args['text']}"


@skill("reminder_list", doc="Show upcoming reminders")
def reminder_list(args, ctx):
    items = [i for i in store.load("reminders", []) if not i["fired"]]
    if not items:
        return "You have no upcoming reminders."
    items.sort(key=lambda i: i["when"])
    return "Upcoming: " + "; ".join(f"{i['when']} - {i['text']}" for i in items[:8])


class ReminderService(threading.Thread):
    """Background thread: checks every few seconds and calls notify(text, lang) for due reminders."""

    def __init__(self, notify, interval=15):
        super().__init__(daemon=True)
        self.notify, self.interval = notify, interval
        self._halt = threading.Event()

    def check(self):
        due = []
        with _LOCK:
            items = store.load("reminders", [])
            now = _local_now()
            for i in items:
                if not i["fired"] and datetime.strptime(i["when"], TIME_FMT) <= now:
                    i["fired"] = True
                    due.append(i)
            if due:
                store.save("reminders", items)
        for i in due:
            self.notify(i["text"], i.get("lang", "en"))

    def run(self):
        while not self._halt.wait(self.interval):
            try:
                self.check()
            except Exception:
                pass

    def stop(self):
        self._halt.set()


# ---------------- Routine and report ----------------
ROUTINE_SYSTEM = (
    "You plan realistic daily routines. Output plain text only, one line per block in the form "
    "'HH:MM - activity', at most 14 lines, no markdown. Include meals, study or work blocks, "
    "exercise, rest and sleep. Respect any wake or sleep time given."
)


@skill("daily_routine", optional=("goals", "wake_time", "sleep_time"),
       doc="Create a daily routine plan (also uses the pending to-do list)")
def daily_routine(args, ctx):
    pending = [t["task"] for t in store.load("todos", []) if not t["done"]][:8]
    request = (f"Goals: {args.get('goals', 'not given')}. Wake: {args.get('wake_time', '07:00')}. "
               f"Sleep: {args.get('sleep_time', '23:00')}. Pending tasks to fit in: {pending or 'none'}.")
    plan = ctx.llm(ROUTINE_SYSTEM, request)
    store.save("routine", {"created": store.now_iso(), "plan": plan})
    return plan


@skill("daily_report", doc="Report what was done today: tasks, reminders and commands")
def daily_report(args, ctx):
    today = store.now().date().isoformat()
    todos = store.load("todos", [])
    done = [t["task"] for t in todos if t["done"] and (t.get("done_at") or "").startswith(today)]
    pending = [t["task"] for t in todos if not t["done"]]
    reminders = [i["text"] for i in store.load("reminders", []) if i["when"].startswith(today)]
    commands = [h for h in store.load("history", []) if h["ts"].startswith(today)]
    parts = [f"Today: {len(commands)} commands given",
             f"{len(done)} tasks done" + (f" ({'; '.join(done[:5])})" if done else ""),
             f"{len(pending)} tasks pending" + (f" ({'; '.join(pending[:5])})" if pending else ""),
             f"{len(reminders)} reminders today"]
    return ". ".join(parts) + "."
