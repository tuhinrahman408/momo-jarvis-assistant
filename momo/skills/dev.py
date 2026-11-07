"""Programming assistant: write, explain and debug code, create projects and websites, run scripts, use git."""
import subprocess
import sys

from .. import config
from ..util import open_url, safe_name, strip_code_fences
from .paths import user_path
from .registry import skill

EXT = {"python": ".py", "javascript": ".js", "java": ".java", "c": ".c", "cpp": ".cpp",
       "html": ".html", "css": ".css", "typescript": ".ts", "bash": ".sh"}
CODE_SYSTEM = "You are an expert programmer. Return ONLY the complete code, no explanation, and no markdown fences."
EXPLAIN_SYSTEM = "Explain what this code does, step by step, in at most 150 words of plain text."
DEBUG_SYSTEM = ("Find the bugs in this code. First give a short plain-text explanation (max 80 words). Then a line "
                "'===CODE===', then the full corrected file, then a line '===END==='.")
SITE_SYSTEM = ("Create a complete, good-looking, responsive website as ONE self-contained HTML file with inline CSS and "
               "JavaScript. Return ONLY the HTML, no explanation, no markdown fences.")
MUTATING_GIT = {"commit", "push", "pull", "clone", "init"}


def _read(path: str, limit: int = 20000) -> tuple:
    p = user_path(path, config.WORKSPACE / "code")
    if not p.is_file():
        raise FileNotFoundError(f"{p} is not a file.")
    return p, p.read_text(encoding="utf-8", errors="replace")[:limit]


@skill("code_write", required=("task",), optional=("language", "filename"), doc="Write a program for a task and save it as a file")
def code_write(args, ctx):
    lang = args.get("language", "python").lower()
    code = strip_code_fences(ctx.llm(CODE_SYSTEM, f"Language: {lang}\nTask: {args['task']}"))
    folder = config.WORKSPACE / "code"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / safe_name(args.get("filename") or args["task"][:40], EXT.get(lang, ".txt"))
    path.write_text(code, encoding="utf-8")
    return f"Wrote the code to {path} ({len(code.splitlines())} lines)."


@skill("code_explain", required=("path",), doc="Explain what a code file does")
def code_explain(args, ctx):
    _, code = _read(args["path"])
    return ctx.llm(EXPLAIN_SYSTEM, code)


@skill("code_debug", required=("path",), optional=("error",),
       doc="Find and fix bugs in a code file (the fixed copy is saved next to it as a new file)")
def code_debug(args, ctx):
    p, code = _read(args["path"])
    out = ctx.llm(DEBUG_SYSTEM, f"Error message: {args.get('error', 'not given')}\n\nCode:\n{code}")
    explanation, _, rest = out.partition("===CODE===")
    fixed = rest.partition("===END===")[0]
    if not fixed.strip():
        return explanation.strip() or "I could not find a fix."
    target = p.with_name(p.stem + ".fixed" + p.suffix)
    target.write_text(strip_code_fences(fixed), encoding="utf-8")
    return f"{explanation.strip()} The corrected file is saved as {target.name}; your original is unchanged."


@skill("project_create", required=("name",), optional=("kind",), doc="Create a starter project folder (kind = python or web)")
def project_create(args, ctx):
    root = user_path(args["name"], config.WORKSPACE / "projects")
    kind = args.get("kind", "python").lower()
    root.mkdir(parents=True, exist_ok=True)
    files = ({"index.html": "<!doctype html>\n<html><head><meta charset=\"utf-8\"><title>%s</title>"
                            "<link rel=\"stylesheet\" href=\"style.css\"></head>\n<body><h1>%s</h1>"
                            "<script src=\"script.js\"></script></body></html>\n" % (root.name, root.name),
              "style.css": "body { font-family: sans-serif; margin: 2rem; }\n",
              "script.js": "console.log('ready');\n"}
             if kind == "web" else
             {"main.py": "def main():\n    print('Hello from %s')\n\n\nif __name__ == '__main__':\n    main()\n" % root.name,
              "requirements.txt": "", "README.md": f"# {root.name}\n"})
    for name, content in files.items():
        f = root / name
        if not f.exists():
            f.write_text(content, encoding="utf-8")
    return f"Created the {kind} project: {root}"


@skill("site_create", required=("description",), optional=("name",), doc="Build a website from a description and open it in the browser")
def site_create(args, ctx):
    html = strip_code_fences(ctx.llm(SITE_SYSTEM, args["description"]))
    root = user_path(args.get("name") or args["description"][:30], config.WORKSPACE / "projects")
    root.mkdir(parents=True, exist_ok=True)
    page = root / "index.html"
    page.write_text(html, encoding="utf-8")
    if ctx.browser is not None:
        open_url(ctx.browser, page.as_uri())
    return f"Built the website and opened it: {page}"


@skill("code_run", required=("path",), risky=True, doc="Run a Python file and show its output (30 second limit)")
def code_run(args, ctx):
    p, _ = _read(args["path"])
    if p.suffix != ".py":
        raise ValueError("I can only run Python (.py) files.")
    try:
        r = subprocess.run([sys.executable, str(p)], cwd=str(p.parent), capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return "The program ran longer than 30 seconds, so I stopped it."
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    return f"Exit code {r.returncode}. " + (out[-1200:] if out else "No output.")


@skill("git", required=("op",), optional=("repo", "message", "url"),
       doc="Git helper (op = status, log, init, commit, push, pull, clone); repo is a folder inside the home folder")
def git(args, ctx):
    op = args["op"].lower()
    if op not in {"status", "log"} | MUTATING_GIT:
        raise ValueError("Git op must be status, log, init, commit, push, pull or clone.")
    if op in MUTATING_GIT and not ctx.confirm(f"Run git {op}? (y/n)"):
        return "Okay, I did not run it."
    if op == "clone":
        url = args.get("url", "")
        if not url.startswith("https://github.com/"):
            raise ValueError("I can only clone https://github.com/ repositories.")
        dest = config.WORKSPACE / "repos"
        dest.mkdir(parents=True, exist_ok=True)
        cmd, cwd = ["git", "clone", url], dest
    else:
        cwd = user_path(args.get("repo", "."), config.WORKSPACE / "projects") if args.get("repo") else config.WORKSPACE
        if op == "commit":
            subprocess.run(["git", "add", "-A"], cwd=cwd, check=True, capture_output=True)
            cmd = ["git", "commit", "-m", args.get("message") or "Update"]
        else:
            cmd = ["git", op] + (["--oneline", "-n", "5"] if op == "log" else [])
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    return f"git {op}: " + (out[-800:] if out else "done.")
