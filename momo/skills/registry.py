"""Skill registry. Every local ability (PC control, to-do list, office files, ...) is a small
function registered here. The LLM can only pick a skill by name from this registry, and Python
validates the arguments, so the model never runs arbitrary commands."""
from dataclasses import dataclass
from typing import Callable

from ..errors import Cancelled

SKILLS: dict = {}


@dataclass
class Skill:
    name: str
    fn: Callable
    required: tuple
    optional: tuple
    risky: bool
    doc: str


@dataclass
class Ctx:
    """Everything a skill may need at run time."""
    lang: str = "en"
    confirm: Callable = lambda question: False
    browser: object = None
    llm: Callable = None          # llm(system, user) -> text
    say: Callable = print


def skill(name, required=(), optional=(), risky=False, doc=""):
    def deco(fn):
        SKILLS[name] = Skill(name, fn, tuple(required), tuple(optional), bool(risky), doc)
        return fn
    return deco


def catalog_text() -> str:
    """Skill list injected into the system prompt, so prompt and code never drift apart."""
    lines = []
    for s in SKILLS.values():
        args = ", ".join(list(s.required) + [f"{o}?" for o in s.optional])
        flag = " [asks for confirmation]" if s.risky else ""
        lines.append(f"- {s.name}({args}): {s.doc}{flag}")
    return "\n".join(lines)


def run_skill(name: str, args: dict, ctx: Ctx) -> str:
    s = SKILLS[name]
    if s.risky and not ctx.confirm(f"Please confirm: {s.doc}. Details: {args}. Proceed? (y/n)"):
        raise Cancelled()
    return s.fn(args, ctx)
