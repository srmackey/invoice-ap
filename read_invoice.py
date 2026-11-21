"""Read one supplier invoice.

Reads the PDF with Amazon Textract AnalyzeExpense (the default) or with a
local Docling Serve (the backup), and returns the seven fields plus the
exception rule from DESIGN.md. The page, sandbox/score.py, and the hosted
function call this same code.

Usage:
  python read_invoice.py path/to/invoice.pdf
  python read_invoice.py path/to/invoice.pdf --profile <profile>
  python read_invoice.py path/to/invoice.pdf --runner docling

Textract needs boto3 and AWS credentials (a profile, or the environment).
Docling needs the container in services/docling running.

The command appends one record to reads/reads.jsonl in this checkout.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone
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
RUNNERS = ("textract", "docling")
DEFAULT_RUNNER = "textract"
DEFAULT_REGION = "us-east-1"
TEXTRACT_MODEL = "textract-analyze-expense"
TEXTRACT_TYPES = {
    "VENDOR_NAME": "supplier",
    "INVOICE_RECEIPT_ID": "invoice_number",
    "INVOICE_RECEIPT_DATE": "invoice_date",
    "DUE_DATE": "due_date",
    "SUBTOTAL": "subtotal",
    "TAX": "tax",
    "TOTAL": "total",
    "AMOUNT_DUE": "amount_due",
    "PAYMENT_TERMS": "terms",
}
# Read alongside the seven, and used only to fill them: the printed amount due
# stands in for a missing total, and printed terms set a missing due date.
EXTRAS = ("amount_due", "terms")
RAW_KEYS = FIELDS + EXTRAS
TERMS_PREFIXES = (("terms", "payment terms"), ("terms", "terms"))
AMOUNT_DUE_PREFIXES = ("amount due", "balance due", "total due")
TERMS_LINE_MAX = 40


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


UNCOMPUTABLE = "uncomputable"
_EOM = re.compile(r"\beom\b|\be\.o\.m\b|end of (?:the )?month|\bprox(?:imo)?\b|\bmfi\b")
_RECEIPT = re.compile(r"\b(?:up)?on receipt\b")
_DISCOUNT_NET = re.compile(r"\d+(?:\.\d+)?\s*%?\s*/\s*\d{1,3}\s*,?\s*n(?:et)?\s*/?\s*(\d{1,3})\b(?![.,]\d)")
_NET = re.compile(r"\bnet\s*/?\s*(\d{1,3})\b(?![.,]\d)")


def terms_days(text) -> int | str | None:
    """Days from the invoice date to the due date under printed terms.

    "Net N" and "2/10 Net 30" give N; "Due on receipt" gives 0. End-of-month
    terms give UNCOMPUTABLE, which leaves the due date for a person. Anything
    else is not terms the rule knows, and gives None."""
    if blank(text):
        return None
    lowered = squashed(text).lower()
    if _EOM.search(lowered):
        return UNCOMPUTABLE
    if _RECEIPT.search(lowered):
        return 0
    match = _DISCOUNT_NET.search(lowered) or _NET.search(lowered)
    if match:
        return int(match.group(1))
    return None


def due_from_terms(invoice_date, due_raw, due_value, terms_raw):
    """The due date after printed terms. A printed due date wins, even one that
    does not parse. Terms printed where the due date goes ("Due Date: Upon
    receipt") count as terms."""
    if isinstance(due_value, date):
        return due_value
    days = terms_days(due_raw if due_value == "unparsed" else terms_raw)
    if days is None:
        return due_value
    if days == UNCOMPUTABLE or not isinstance(invoice_date, date):
        return None
    return invoice_date + timedelta(days=days)


def parsed(raw: dict) -> dict:
    out = {}
    for name in TEXT_FIELDS:
        out[name] = as_text(raw.get(name))
    for name in DATE_FIELDS:
        out[name] = as_date(raw.get(name))
    for name in MONEY_FIELDS:
        out[name] = as_money(raw.get(name))
    if out["total"] is None:
        out["total"] = as_money(raw.get("amount_due"))
    out["due_date"] = due_from_terms(out["invoice_date"], raw.get("due_date"), out["due_date"], raw.get("terms"))
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


BARE_LABELS = (
    {prefix for _name, prefix in FIELD_PREFIXES}
    | {prefix for _name, prefix in TERMS_PREFIXES}
    | set(AMOUNT_DUE_PREFIXES)
    | set(MONEY_LABELS)
    | HEADER_SKIP
)


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


def take_terms(fields: dict, text: str, following: str | None) -> None:
    """Printed payment terms: after a "Terms" label, or a short line that is
    terms on its own ("Due on receipt", "Net 30")."""
    if fields["terms"] is not None:
        return
    lowered = text.lower()
    for _name, prefix in TERMS_PREFIXES:
        if lowered.strip(" :.-") == prefix:
            candidate = following if following and not bare_label(following) else None
        elif lowered.startswith(prefix):
            candidate = text[len(prefix) :].strip(" :.-")
        else:
            continue
        if candidate and terms_days(candidate) is not None:
            fields["terms"] = candidate
            return
    if len(text) <= TERMS_LINE_MAX and terms_days(text) is not None:
        fields["terms"] = text


def take_amount_due(fields: dict, text: str, following: str | None) -> None:
    if fields["amount_due"] is not None:
        return
    lowered = text.lower()
    if not any(lowered.startswith(prefix) for prefix in AMOUNT_DUE_PREFIXES):
        return
    amounts = money_amounts(text)
    if not amounts and following and not bare_label(following):
        amounts = money_amounts(following)
    if amounts:
        fields["amount_due"] = amounts[0]


def take_all(fields: dict, text: str, following: str | None) -> None:
    take_labeled(fields, text, following)
    take_money(fields, text, following)
    take_terms(fields, text, following)
    take_amount_due(fields, text, following)


def take_row(fields: dict, cells: dict) -> None:
    ordered = sorted(cells.items())
    for index, (_col, text) in enumerate(ordered):
        following = ordered[index + 1][1] if index + 1 < len(ordered) else None
        take_all(fields, text, following)


def fields_from_docling(doc: dict) -> dict:
    fields = {name: None for name in RAW_KEYS}
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
        take_all(fields, text, following)
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


def textract_fields(response: dict) -> dict:
    """Map AnalyzeExpense summary fields to the seven, plus the amount due and
    the payment terms. For each, the detection with the highest value
    confidence wins. When no PAYMENT_TERMS came back, terms Textract leaves
    untyped count: an OTHER field under a label with "terms" in it, then a
    short line of page text that is terms on its own ("Due on receipt")."""
    best: dict[str, tuple[float, str]] = {}
    loose_terms: tuple[float, str] | None = None
    terms_line: str | None = None
    for document in response.get("ExpenseDocuments") or []:
        for block in document.get("Blocks") or []:
            text = block.get("Text")
            if terms_line is None and block.get("BlockType") == "LINE" and isinstance(text, str):
                if len(text) <= TERMS_LINE_MAX and terms_days(text) is not None:
                    terms_line = text
        for field in document.get("SummaryFields") or []:
            kind = (field.get("Type") or {}).get("Text")
            name = TEXTRACT_TYPES.get(kind)
            detection = field.get("ValueDetection") or {}
            value = detection.get("Text")
            if not isinstance(value, str):
                continue
            confidence = float(detection.get("Confidence") or 0)
            if name is None:
                label = ((field.get("LabelDetection") or {}).get("Text") or "").lower()
                if kind == "OTHER" and "terms" in label and terms_days(value) is not None:
                    if loose_terms is None or confidence > loose_terms[0]:
                        loose_terms = (confidence, value)
                continue
            if name not in best or confidence > best[name][0]:
                best[name] = (confidence, value)
    if "terms" not in best and loose_terms is not None:
        best["terms"] = loose_terms
    if "terms" not in best and terms_line is not None:
        best["terms"] = (0.0, terms_line)
    return {name: best[name][1] if name in best else None for name in RAW_KEYS}


_CLIENTS: dict[tuple, object] = {}


def textract_client(profile: str | None = None, region: str | None = None):
    """One client per profile and region for the life of the process.
    Region falls back to AWS_REGION (set inside Lambda), then us-east-1."""
    region = region or os.environ.get("AWS_REGION") or DEFAULT_REGION
    key = (profile, region)
    if key not in _CLIENTS:
        import boto3

        _CLIENTS[key] = boto3.Session(profile_name=profile, region_name=region).client("textract")
    return _CLIENTS[key]


def textract_read(pdf: Path, client=None, profile: str | None = None) -> tuple[dict | None, str, str | None, int]:
    """Returns (fields, runner, error, pages). A failure is the error, not a raise."""
    try:
        client = client or textract_client(profile)
        response = client.analyze_expense(Document={"Bytes": pdf.read_bytes()})
    except ImportError:
        return None, TEXTRACT_MODEL, "textract needs boto3: pip install boto3", 0
    except Exception as exc:
        return None, TEXTRACT_MODEL, f"textract request failed: {exc}", 0
    pages = int((response.get("DocumentMetadata") or {}).get("Pages") or 0)
    return textract_fields(response), TEXTRACT_MODEL, None, pages


def read_fields(
    pdf: Path, runner: str = DEFAULT_RUNNER, client=None, profile: str | None = None
) -> tuple[dict | None, str, str | None, int]:
    """The raw seven from one runner: (fields, runner, error, pages)."""
    if runner == "textract":
        return textract_read(pdf, client, profile)
    if runner == "docling":
        got_raw, used, error = docling_read(pdf)
        return got_raw, used, error, 0
    raise ValueError(f"unknown runner {runner}")


def read_invoice(
    pdf: Path, runner: str = DEFAULT_RUNNER, client=None, profile: str | None = None
) -> dict:
    got_raw, used, error, pages = read_fields(pdf, runner, client, profile)
    values = {name: None for name in FIELDS} if got_raw is None else parsed(got_raw)
    reasons = exception_reasons(values)
    out = {name: show(values[name]) for name in FIELDS}
    out["exception"] = bool(reasons)
    out["exception_reasons"] = reasons
    out["error"] = error
    out["runner"] = used
    out["pages"] = pages
    return out


def make_record(pdf: Path, result: dict, seconds: float) -> dict:
    record = {
        "file_name": pdf.name,
        "id": uuid.uuid4().hex,
        "read_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(seconds, 1),
        "runner": result.get("runner"),
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
    parser.add_argument("--runner", choices=RUNNERS, default=DEFAULT_RUNNER)
    parser.add_argument("--profile", help="AWS profile for the textract runner.")
    args = parser.parse_args()
    pdf = args.pdf
    if not pdf.is_file():
        raise SystemExit(f"no file {pdf}")
    started = time.perf_counter()
    result = read_invoice(pdf, args.runner, profile=args.profile)
    record = make_record(pdf, result, time.perf_counter() - started)
    append_record(record)
    print(json.dumps(result, indent=2))
    if result["error"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
