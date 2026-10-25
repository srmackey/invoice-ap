"""Read one supplier invoice.

Sends the PDF to Docling Serve and returns the seven fields plus the
exception rule from AGENTS.md. sandbox/score.py calls this same code.

Usage:
  python read_invoice.py path/to/invoice.pdf

The command appends one record to reads/reads.jsonl in this checkout.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

FIELDS = (
    "supplier",
    "invoice_number",
    "invoice_date",
    "due_date",
    "subtotal",
    "tax",
    "total",
)
TEXT_FIELDS = ("supplier", "invoice_number")
DATE_FIELDS = ("invoice_date", "due_date")
MONEY_FIELDS = ("subtotal", "tax", "total")
CENT = Decimal("0.01")
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y")
DOCLING_URL = "http://127.0.0.1:5001/v1/convert/file"
DOCLING_TIMEOUT = 120
READS_PATH = Path(__file__).resolve().parent / "reads" / "reads.jsonl"
MONEY_LABELS = ("subtotal", "tax", "total")
HEADER_SKIP = {"invoice", "bill to"}
FIELD_PREFIXES = (
    ("invoice_number", "invoice number"),
    ("invoice_date", "invoice date"),
    ("due_date", "due date"),
)


def blank(value) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return text == "" or text.lower() in {"null", "none", "n/a"}


def as_text(value):
    if blank(value):
        return None
    return " ".join(str(value).split())


def as_date(value):
    text = as_text(value)
    if text is None:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return "unparsed"


def as_money(value):
    if blank(value):
        return None
    text = str(value).strip().lstrip(">").replace("$", "").replace(",", "").strip()
    try:
        return Decimal(text).quantize(CENT)
    except (InvalidOperation, ValueError):
        return "unparsed"


def parsed(raw: dict) -> dict:
    out = {}
    for name in TEXT_FIELDS:
        out[name] = as_text(raw.get(name))
    for name in DATE_FIELDS:
        out[name] = as_date(raw.get(name))
    for name in MONEY_FIELDS:
        out[name] = as_money(raw.get(name))
    return out


def exception_reasons(values: dict) -> list[str]:
    reasons = []
    for name in FIELDS:
        if values[name] is None:
            reasons.append(f"missing_{name}")
    invoice_date = values["invoice_date"]
    due_date = values["due_date"]
    if isinstance(invoice_date, date) and isinstance(due_date, date) and due_date < invoice_date:
        reasons.append("due_before_invoice")
    amounts = [values[name] for name in MONEY_FIELDS]
    if all(isinstance(amount, Decimal) for amount in amounts):
        subtotal, tax, total = amounts
        if abs(subtotal + tax - total) > CENT:
            reasons.append("total_mismatch")
    return reasons


def show(value) -> str | None:
    if value is None or value == "unparsed":
        return None if value is None else "unparsed"
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    return str(value)


def squashed(value) -> str:
    return " ".join(str(value).split())


def money_label_words(text: str) -> list[str] | None:
    words = re.findall(r"[A-Za-z]+", text)
    if not words:
        return None
    keys = [word.lower() for word in words]
    if any(key not in MONEY_LABELS for key in keys):
        return None
    return keys


def money_amounts(text: str) -> list[str]:
    return re.findall(r"\$?\d[\d,]*\.\d{2}", text)


BARE_LABELS = {prefix for _name, prefix in FIELD_PREFIXES} | set(MONEY_LABELS) | HEADER_SKIP


def bare_label(text: str) -> bool:
    return text.lower().strip(" :.-") in BARE_LABELS


def take_labeled(fields: dict, text: str, following: str | None) -> None:
    lowered = text.lower()
    for name, prefix in FIELD_PREFIXES:
        if fields[name] is not None:
            continue
        if lowered == prefix and following and not bare_label(following):
            fields[name] = following
            continue
        if lowered.startswith(prefix):
            rest = text[len(prefix) :].strip(" :.-")
            if rest and not bare_label(rest):
                fields[name] = rest


def take_money(fields: dict, text: str, following: str | None) -> None:
    labels = money_label_words(text)
    if not labels:
        return
    amounts = money_amounts(text)
    if len(amounts) < len(labels) and following and not bare_label(following):
        amounts = money_amounts(following)
    for label, amount in zip(labels, amounts):
        if fields[label] is None:
            fields[label] = amount


def take_row(fields: dict, cells: dict) -> None:
    ordered = sorted(cells.items())
    for index, (_col, text) in enumerate(ordered):
        following = ordered[index + 1][1] if index + 1 < len(ordered) else None
        take_labeled(fields, text, following)
        take_money(fields, text, following)


def fields_from_docling(doc: dict) -> dict:
    fields = {name: None for name in FIELDS}
    texts = []
    for item in doc.get("texts") or []:
        text = squashed(item.get("text") or "")
        if not text:
            continue
        texts.append(text)
        if fields["supplier"] is None and item.get("label") == "section_header":
            if text.lower() not in HEADER_SKIP:
                fields["supplier"] = text
    for index, text in enumerate(texts):
        following = texts[index + 1] if index + 1 < len(texts) else None
        take_labeled(fields, text, following)
        take_money(fields, text, following)
    for table in doc.get("tables") or []:
        rows: dict[int, dict[int, str]] = {}
        for cell in (table.get("data") or {}).get("table_cells") or []:
            text = squashed(cell.get("text") or "")
            if not text:
                continue
            row = cell.get("start_row_offset_idx")
            col = cell.get("start_col_offset_idx")
            if not isinstance(row, int) or not isinstance(col, int):
                continue
            rows.setdefault(row, {})[col] = text
        for cells in rows.values():
            take_row(fields, cells)
    return fields


def docling_read(pdf: Path) -> tuple[dict | None, str, str | None]:
    boundary = f"iap{uuid.uuid4().hex}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="files"; filename="{pdf.name}"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode() + pdf.read_bytes() + (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="to_formats"\r\n\r\n'
        f"json\r\n--{boundary}--\r\n"
    ).encode()
    req = urllib.request.Request(
        DOCLING_URL,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=DOCLING_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except TimeoutError:
        return None, "docling", f"timed out after {DOCLING_TIMEOUT}s"
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        return None, "docling", f"docling request failed: {reason}"
    if not isinstance(payload, dict):
        return None, "docling", "docling output was not JSON"
    if payload.get("status") != "success":
        errors = payload.get("errors")
        return None, "docling", f"docling status {payload.get('status')}: {errors}"
    document = payload.get("document") if isinstance(payload.get("document"), dict) else {}
    content = document.get("json_content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            return None, "docling", "docling document was not JSON"
    if not isinstance(content, dict):
        return None, "docling", "docling returned no document"
    return fields_from_docling(content), "docling", None


def read_invoice(pdf: Path) -> dict:
    got_raw, _used, error = docling_read(pdf)
    values = {name: None for name in FIELDS} if got_raw is None else parsed(got_raw)
    reasons = exception_reasons(values)
    out = {name: show(values[name]) for name in FIELDS}
    out["exception"] = bool(reasons)
    out["exception_reasons"] = reasons
    out["error"] = error
    return out


def make_record(pdf: Path, result: dict, seconds: float) -> dict:
    record = {
        "file_name": pdf.name,
        "id": uuid.uuid4().hex,
        "read_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(seconds, 1),
    }
    for name in FIELDS:
        record[name] = result[name]
    record["exception"] = result["exception"]
    record["exception_reasons"] = list(result["exception_reasons"])
    record["error"] = result["error"]
    return record


def append_record(record: dict) -> None:
    READS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with READS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Read one supplier invoice.")
    parser.add_argument("pdf", type=Path)
    args = parser.parse_args()
    pdf = args.pdf
    if not pdf.is_file():
        raise SystemExit(f"no file {pdf}")
    started = time.perf_counter()
    result = read_invoice(pdf)
    record = make_record(pdf, result, time.perf_counter() - started)
    append_record(record)
    print(json.dumps(result, indent=2))
    if result["error"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
