"""AI memory: facts you ask Momo to remember, project notes, command history, suggestions.
Everything is stored locally in plain JSON files inside the workspace data folder."""
from . import store
from .registry import skill

MAX_HISTORY = 500


def log_command(text: str) -> None:
    try:
        history = store.load("history", [])
        history.append({"ts": store.now_iso(), "text": text[:300]})
        store.save("history", history[-MAX_HISTORY:])
    except Exception:
        pass


def chat_context() -> str:
    """Short text added to the chat prompt so replies can use what the user asked to remember."""
    facts = store.load("facts", [])[-15:]
    if not facts:
        return ""
    return "Things the user asked you to remember:\n" + "\n".join(f"- {f['text']}" for f in facts)


@skill("remember", required=("text",), doc="Remember a fact or preference the user tells you")
def remember(args, ctx):
    facts = store.load("facts", [])
    facts.append({"text": args["text"], "ts": store.now_iso()})
    store.save("facts", facts)
    return f"Okay, I will remember: {args['text']}"


@skill("recall", optional=("query",), doc="Tell the user what you remember (optionally about a topic)")
def recall(args, ctx):
    facts = store.load("facts", [])
    q = args.get("query", "").lower()
    if q:
        words = [w for w in q.split() if len(w) > 2] or [q]
        facts = [f for f in facts if any(w in f["text"].lower() for w in words)]
    if not facts:
        return "I don't remember anything about that yet."
    return "I remember: " + "; ".join(f["text"] for f in facts[-10:])


@skill("forget", required=("text",), risky=True, doc="Forget remembered facts that contain the given text")
def forget(args, ctx):
    key = args["text"].lower()
    facts = store.load("facts", [])
    kept = [f for f in facts if key not in f["text"].lower()]
    store.save("facts", kept)
    return f"Forgot {len(facts) - len(kept)} item(s)."


@skill("project_note", required=("name", "note"), doc="Save a note about a project")
def project_note(args, ctx):
    projects = store.load("projects", {})
    projects.setdefault(args["name"], []).append({"note": args["note"], "ts": store.now_iso()})
    store.save("projects", projects)
    return f"Saved a note for project {args['name']}."


@skill("project_info", optional=("name",), doc="Give information about an old project (or list all projects)")
def project_info(args, ctx):
    projects = store.load("projects", {})
    name = args.get("name", "").strip()
    if not name:
        return ("Projects: " + ", ".join(projects)) if projects else "I have no project notes yet."
    key = next((k for k in projects if k.lower() == name.lower() or name.lower() in k.lower()), None)
    notes = [n["note"] for n in projects.get(key, [])]
    related = [h["text"] for h in store.load("history", []) if name.lower() in h["text"].lower()][-5:]
    if not notes and not related:
        return f"I have nothing about {name}."
    out = []
    if notes:
        out.append("Notes: " + "; ".join(notes[-8:]))
    if related:
        out.append("Related commands: " + "; ".join(related))
    return " ".join(out)


SUGGEST_SYSTEM = (
    "You give short, practical productivity suggestions. Output plain text, at most 3 numbered "
    "suggestions, each one sentence, based on the user's recent commands and remembered facts."
)


@skill("suggest", doc="Give suggestions based on how the user usually works")
def suggest(args, ctx):
    history = [h["text"] for h in store.load("history", [])[-40:]]
    if not history:
        return "I need to see a few more of your commands first."
    facts = [f["text"] for f in store.load("facts", [])[-10:]]
    return ctx.llm(SUGGEST_SYSTEM, f"Recent commands: {history}\nRemembered facts: {facts}")
