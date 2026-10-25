"""Show invoice reads on one local page.

Reads reads/reads.jsonl from this checkout. The page accepts a PDF and
calls the same reader as read_invoice.py. Standard library only.

  python page.py

Then open http://127.0.0.1:8765
"""

from __future__ import annotations

import json
import tempfile
import time
import urllib.parse
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from read_invoice import FIELDS, READS_PATH, append_record, make_record, read_invoice

HOST = "127.0.0.1"
PORT = 8765
MAX_PDF = 32 * 1024 * 1024

LABELS = {
    "supplier": "Supplier",
    "invoice_number": "Invoice number",
    "invoice_date": "Invoice date",
    "due_date": "Due date",
    "subtotal": "Subtotal",
    "tax": "Tax",
    "total": "Total",
}
REASONS = {f"missing_{name}": f"{LABELS[name]} is missing" for name in FIELDS}
REASONS["due_before_invoice"] = "Due date is before the invoice date"
REASONS["total_mismatch"] = "Subtotal plus tax does not match the total"

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ font: 16px/1.45 system-ui, sans-serif; margin: 2rem auto; max-width: 40rem; padding: 0 1rem; color: #1c1c1c; }}
  a {{ color: #0b3d91; }}
  .numbers p {{ margin: 0.15rem 0; font-size: 1.15rem; }}
  ul {{ list-style: none; padding: 0; margin: 1.5rem 0 0; }}
  li {{ padding: 0.75rem 0; border-top: 1px solid #ddd; }}
  .meta {{ color: #444; }}
  dl {{ display: grid; grid-template-columns: minmax(8rem, 11rem) 1fr; gap: 0.35rem 1rem; margin: 1rem 0; }}
  dt {{ color: #444; }}
  dd {{ margin: 0; }}
  .note {{ color: #444; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def load_reads(path: Path | None = None) -> tuple[list[dict], int]:
    src = READS_PATH if path is None else path
    if not src.is_file():
        return [], 0
    reads = []
    skipped = 0
    for line in src.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if not isinstance(item, dict) or not item.get("id"):
            skipped += 1
            continue
        reads.append(item)
    return reads, skipped


def summary(reads: list[dict]) -> dict | None:
    if not reads:
        return None
    clear = sum(1 for row in reads if row.get("exception") is False)
    elapsed = 0.0
    for row in reads:
        try:
            elapsed += float(row.get("seconds"))
        except (TypeError, ValueError):
            continue
    return {
        "count": len(reads),
        "clear": clear,
        "seconds": round(elapsed / len(reads), 1),
    }


def reason_text(code: str) -> str:
    return REASONS.get(code, code)


def numbers_html(reads: list[dict]) -> str:
    got = summary(reads)
    if got is None:
        return "<p>No reads yet.</p>"
    return (
        '<div class="numbers">'
        f"<p>{got['clear']} of {got['count']} raised no exception</p>"
        f"<p>{got['seconds']:.1f} seconds per invoice</p>"
        "</div>"
    )


def row_state(row: dict) -> str:
    if row.get("error"):
        return "Error"
    if row.get("exception") is True:
        return "Exception"
    return "Clear"


def show_value(value) -> str:
    if value is None or value == "":
        return "Missing"
    return escape(str(value))


def layout(title: str, body: str) -> str:
    return PAGE.format(title=escape(title), body=body)


def upload_form() -> str:
    return (
        '<form action="/read" method="post" enctype="multipart/form-data">'
        '<label for="pdf">PDF</label> '
        '<input id="pdf" type="file" name="pdf" accept="application/pdf,.pdf" required> '
        '<button type="submit">Read</button>'
        "</form>"
    )


def render_list(reads: list[dict], skipped: int) -> str:
    parts = ["<h1>Invoice reads</h1>", upload_form(), numbers_html(reads)]
    if skipped:
        word = "line" if skipped == 1 else "lines"
        parts.append(f'<p class="note">{skipped} {word} could not be read.</p>')
    if reads:
        items = []
        for row in reversed(reads):
            name = show_value(row.get("file_name"))
            when = show_value(row.get("read_at"))
            state = row_state(row)
            try:
                seconds = f"{float(row.get('seconds')):.1f}s"
            except (TypeError, ValueError):
                seconds = "Missing"
            ident = urllib.parse.quote(str(row["id"]))
            items.append(
                "<li>"
                f'<a href="/read/{ident}">{name}</a>'
                f'<div class="meta">{when} · {escape(state)} · {escape(seconds)}</div>'
                "</li>"
            )
        parts.append("<ul>" + "".join(items) + "</ul>")
    return layout("Invoice reads", "\n".join(parts))


def render_one(row: dict, reads: list[dict]) -> str:
    fields = []
    for name in FIELDS:
        fields.append(f"<dt>{escape(LABELS[name])}</dt><dd>{show_value(row.get(name))}</dd>")
    reasons = row.get("exception_reasons") if isinstance(row.get("exception_reasons"), list) else []
    raised = row.get("exception") is True or bool(reasons)
    fields.append(f"<dt>Exception</dt><dd>{'Yes' if raised else 'No'}</dd>")
    if reasons:
        text = "; ".join(reason_text(str(code)) for code in reasons)
        fields.append(f"<dt>Reasons</dt><dd>{escape(text)}</dd>")
    if row.get("error"):
        fields.append(f"<dt>Error</dt><dd>{show_value(row.get('error'))}</dd>")
    body = "\n".join(
        [
            '<p><a href="/">All reads</a></p>',
            f"<h1>{show_value(row.get('file_name'))}</h1>",
            numbers_html(reads),
            f'<p class="meta">{show_value(row.get("read_at"))} · {show_value(row.get("seconds"))} seconds</p>',
            "<dl>",
            *fields,
            "</dl>",
        ]
    )
    return layout(str(row.get("file_name") or "Read"), body)


def render_missing() -> str:
    body = '<p><a href="/">All reads</a></p><h1>That read is not here.</h1>'
    return layout("Not found", body)


def render_choose() -> str:
    body = "<h1>Invoice reads</h1>\n" + upload_form() + "\n<p>Choose a PDF.</p>"
    return layout("Choose a PDF", body)


def safe_pdf_name(filename: str) -> str | None:
    name = Path(filename.replace("\\", "/")).name
    if not name or not name.lower().endswith(".pdf") or len(name) > 200:
        return None
    return name


def boundary_from(content_type: str) -> bytes | None:
    for part in content_type.split(";"):
        bit = part.strip()
        if bit.lower().startswith("boundary="):
            value = bit.split("=", 1)[1].strip()
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]
            if value:
                return value.encode("utf-8", "surrogateescape")
    return None


def parse_pdf_upload(content_type: str, body: bytes) -> tuple[str, bytes] | None:
    boundary = boundary_from(content_type)
    if boundary is None:
        return None
    for chunk in body.split(b"--" + boundary):
        if b"Content-Disposition" not in chunk:
            continue
        header_blob, sep, data = chunk.partition(b"\r\n\r\n")
        if not sep:
            continue
        header = header_blob.decode("utf-8", "replace")
        filename = None
        for line in header.splitlines():
            if "content-disposition" not in line.lower():
                continue
            if 'name="pdf"' not in line and "name=pdf" not in line:
                continue
            for bit in line.split(";"):
                bit = bit.strip()
                if bit.lower().startswith("filename="):
                    filename = bit.split("=", 1)[1].strip().strip('"')
        if filename is None:
            continue
        if data.endswith(b"\r\n"):
            data = data[:-2]
        return filename, data
    return None


def accept_pdf(filename: str, data: bytes) -> dict:
    name = safe_pdf_name(filename)
    if name is None or not data:
        raise ValueError("choose a pdf")
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / name
        dest.write_bytes(data)
        started = time.perf_counter()
        result = read_invoice(dest)
        record = make_record(dest, result, time.perf_counter() - started)
    append_record(record)
    return record


def respond(url_path: str, reads_path: Path | None = None) -> tuple[int, str, str]:
    reads, skipped = load_reads(reads_path)
    path = urllib.parse.urlparse(url_path).path
    if path == "/":
        return 200, "text/html; charset=utf-8", render_list(reads, skipped)
    prefix = "/read/"
    if path.startswith(prefix):
        ident = urllib.parse.unquote(path[len(prefix) :])
        if "/" in ident or ident == "":
            return 404, "text/html; charset=utf-8", render_missing()
        for row in reads:
            if str(row.get("id")) == ident:
                return 200, "text/html; charset=utf-8", render_one(row, reads)
        return 404, "text/html; charset=utf-8", render_missing()
    return 404, "text/html; charset=utf-8", render_missing()


class Handler(BaseHTTPRequestHandler):
    def _write(self, status: int, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        status, _content_type, body = respond(self.path)
        self._write(status, body)

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path != "/read":
            self._write(404, render_missing())
            return
        raw_length = self.headers.get("Content-Length")
        if raw_length is None or not raw_length.isdigit():
            self._write(411, render_choose())
            return
        length = int(raw_length)
        if length > MAX_PDF:
            self.close_connection = True
            self._write(413, layout("Too large", '<h1>That PDF is too large.</h1><p><a href="/">All reads</a></p>'))
            return
        body = self.rfile.read(length)
        parsed = parse_pdf_upload(self.headers.get("Content-Type", ""), body)
        if parsed is None:
            self._write(400, render_choose())
            return
        filename, data = parsed
        if safe_pdf_name(filename) is None or not data:
            self._write(400, render_choose())
            return
        record = accept_pdf(filename, data)
        location = "/read/" + urllib.parse.quote(str(record["id"]))
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt: str, *args) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
