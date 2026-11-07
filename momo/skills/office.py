"""Document and office work: Word, Excel, PowerPoint, PDF summary, email drafts, data conversion."""
import csv
import json
from urllib.parse import quote

from .. import config
from ..util import contact, extract_json, open_url, safe_name
from .paths import user_path
from .registry import skill

DOC_SYSTEM = ("You write clean documents. Output plain text using only this format: a line starting with '# ' is "
              "the title, '## ' starts a heading, '- ' starts a bullet, and a blank line separates paragraphs. "
              "No other markdown.")
SHEET_SYSTEM = ('Return ONLY JSON: {"sheet_name": "...", "headers": ["..."], "rows": [[...], ...]}. Use numbers for '
                'numeric cells. A cell may be an Excel formula string starting with "=" (for example totals).')
SLIDES_SYSTEM = ('Return ONLY JSON: {"title": "...", "slides": [{"title": "...", "bullets": ["...", "..."]}]}. '
                 'Each slide has 3 to 5 short bullets.')
EMAIL_SYSTEM = ('Write a polite, concise email. Return ONLY JSON: {"subject": "...", "body": "..."}. '
                'Write the email in the same language as the request.')
SUMMARY_SYSTEM = "Summarize the document in at most 6 short plain-text points. No markdown."


def _out(folder: str, name: str, ext: str):
    d = config.WORKSPACE / folder
    d.mkdir(parents=True, exist_ok=True)
    return d / safe_name(name, ext)


@skill("doc_create", required=("topic",), optional=("filename", "details"), doc="Write a Word document (.docx) about a topic")
def doc_create(args, ctx):
    from docx import Document
    text = ctx.llm(DOC_SYSTEM, f"Topic: {args['topic']}\nDetails: {args.get('details', 'none')}")
    doc = Document()
    for line in text.splitlines():
        line = line.rstrip()
        if line.startswith("## "):
            doc.add_heading(line[3:], level=2)
        elif line.startswith("# "):
            doc.add_heading(line[2:], level=1)
        elif line.startswith("- "):
            doc.add_paragraph(line[2:], style="List Bullet")
        elif line.strip():
            doc.add_paragraph(line)
    path = _out("documents", args.get("filename") or args["topic"], ".docx")
    doc.save(path)
    return f"Created the Word document: {path}"


@skill("sheet_create", required=("description",), optional=("filename",), doc="Create an Excel sheet (.xlsx) with data and formulas")
def sheet_create(args, ctx):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    data = extract_json(ctx.llm(SHEET_SYSTEM, args["description"]))
    wb = Workbook()
    ws = wb.active
    ws.title = str(data.get("sheet_name") or "Sheet1")[:31]
    ws.append(data.get("headers", []))
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in data.get("rows", []):
        ws.append(row)
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = min(40, max(len(str(c.value or "")) for c in col) + 2)
    path = _out("spreadsheets", args.get("filename") or ws.title, ".xlsx")
    wb.save(path)
    return f"Created the Excel sheet: {path}"


@skill("slides_create", required=("topic",), optional=("filename",), doc="Create a PowerPoint presentation (.pptx) about a topic")
def slides_create(args, ctx):
    from pptx import Presentation
    data = extract_json(ctx.llm(SLIDES_SYSTEM, args["topic"]))
    prs = Presentation()
    title = prs.slides.add_slide(prs.slide_layouts[0])
    title.shapes.title.text = str(data.get("title") or args["topic"])
    for s in data.get("slides", [])[:12]:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = str(s.get("title", ""))
        tf = slide.placeholders[1].text_frame
        bullets = [str(b) for b in s.get("bullets", [])] or [""]
        tf.text = bullets[0]
        for b in bullets[1:]:
            tf.add_paragraph().text = b
    path = _out("presentations", args.get("filename") or args["topic"], ".pptx")
    prs.save(path)
    return f"Created the presentation: {path}"


@skill("pdf_summarize", required=("path",), doc="Read a PDF file and summarize it")
def pdf_summarize(args, ctx):
    from pypdf import PdfReader
    p = user_path(args["path"], config.WORKSPACE / "documents")
    reader = PdfReader(str(p))
    text = "\n".join((page.extract_text() or "") for page in reader.pages[:15])[:15000]
    if not text.strip():
        return "I could not find any text in this PDF (it may be a scanned image)."
    return ctx.llm(SUMMARY_SYSTEM, text)


def _read_table(p):
    ext = p.suffix.lower()
    if ext == ".csv":
        with p.open(newline="", encoding="utf-8-sig") as f:
            return [list(r) for r in csv.reader(f)]
    if ext == ".xlsx":
        from openpyxl import load_workbook
        return [list(r) for r in load_workbook(p, read_only=True, data_only=True).active.iter_rows(values_only=True)]
    if ext == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        if data and isinstance(data[0], dict):
            keys = list(data[0])
            return [keys] + [[d.get(k) for k in keys] for d in data]
        return data
    raise ValueError("Supported formats are csv, xlsx and json.")


@skill("data_convert", required=("path", "to"), doc="Convert a data file between csv, xlsx and json (to = csv, xlsx or json)")
def data_convert(args, ctx):
    src = user_path(args["path"], config.WORKSPACE / "spreadsheets")
    to = args["to"].lower().lstrip(".")
    if to not in ("csv", "xlsx", "json"):
        raise ValueError("Supported formats are csv, xlsx and json.")
    rows = _read_table(src)
    dst = src.with_suffix("." + to)
    if to == "csv":
        with dst.open("w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerows(rows)
    elif to == "xlsx":
        from openpyxl import Workbook
        wb = Workbook()
        for r in rows:
            wb.active.append(r)
        wb.save(dst)
    else:
        head, body = rows[0], rows[1:]
        dst.write_text(json.dumps([dict(zip(head, r)) for r in body], ensure_ascii=False, indent=2), encoding="utf-8")
    return f"Converted to {to}: {dst}"


@skill("email_draft", required=("about",), optional=("to", "subject"),
       doc="Write an email draft and open it in Gmail without sending it")
def email_draft(args, ctx):
    data = extract_json(ctx.llm(EMAIL_SYSTEM, f"Write an email about: {args['about']}. Subject hint: {args.get('subject', 'none')}"))
    subject, body = str(data.get("subject", "")), str(data.get("body", ""))
    to = args.get("to", "")
    if to and "@" not in to:
        to = contact(to, "email") or ""
    url = (f"https://mail.google.com/mail/?view=cm&fs=1&to={quote(to)}"
           f"&su={quote(subject)}&body={quote(body)}")
    if ctx.browser is not None:
        open_url(ctx.browser, url)
    return f"Draft ready (not sent). Subject: {subject}. {body[:200]}"
