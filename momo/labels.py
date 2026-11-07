"""Short task names used in the spoken status line: 'Sir, <task> working right now.'"""

ACTION_LABELS = {"Message": "message", "Call": "call", "Open": "open", "Search": "search",
                 "Play": "play", "Schedule": "calendar", "Fetch": "check"}
SKILL_LABELS = {
    "open_app": "app", "close_app": "app", "lock_pc": "lock", "shutdown_pc": "shutdown",
    "restart_pc": "restart", "cancel_shutdown": "shutdown", "screenshot": "screenshot",
    "volume": "volume", "brightness": "brightness", "type_text": "typing", "press_keys": "keyboard",
    "mouse": "mouse", "doc_create": "document", "sheet_create": "spreadsheet",
    "slides_create": "presentation", "pdf_summarize": "pdf", "data_convert": "conversion",
    "email_draft": "email", "site_create": "website", "git": "git", "remember": "memory",
    "recall": "memory", "forget": "memory", "suggest": "suggestion", "open_browser": "browser",
    "list_browser_profiles": "browser", "daily_routine": "routine", "daily_report": "report",
}
PREFIX_LABELS = (("todo_", "todo"), ("reminder_", "reminder"), ("file_", "file"), ("code_", "code"),
                 ("project_", "project"), ("net_", "network"), ("security_", "security"),
                 ("process_", "security"), ("log_", "security"))


def task_label(cmd: dict) -> str:
    if cmd.get("Action") != "Skill":
        return ACTION_LABELS.get(cmd.get("Action"), "task")
    name = cmd.get("Skill", "")
    if name in SKILL_LABELS:
        return SKILL_LABELS[name]
    for prefix, label in PREFIX_LABELS:
        if name.startswith(prefix):
            return label
    return name.replace("_", " ") or "task"
