"""Score the sandbox invoices with Grok, Claude, Ollama, or local Docling.

The runner returns the seven fields. This script applies the exception rule
and the three scores in ../AGENTS.md. Each call sees only that invoice, so
the runner cannot open labels.json. Ollama receives a rendered page, not a
directory. Docling receives the file and this script reads the converted page.

Usage:
  python score.py --runner grok
  python score.py --runner claude
  python score.py --runner grok --only 01-northline
  python score.py --runner claude --labels hard/labels.json
  python score.py --runner ollama
  python score.py --runner ollama --model qwen3-vl:8b --only 01-northline
  python score.py --runner docling --only 01-northline
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LABELS = ROOT / "labels.json"
RUNS = ROOT / "runs"
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
CALL_TIMEOUT = 180
OLLAMA_TIMEOUT = 600
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_MODEL = "qwen3-vl:8b"
DOCLING_URL = "http://127.0.0.1:5001/v1/convert/file"
DOCLING_TIMEOUT = 120
MONEY_LABELS = ("subtotal", "tax", "total")
HEADER_SKIP = {"invoice", "bill to"}
FIELD_PREFIXES = (
    ("invoice_number", "invoice number"),
    ("invoice_date", "invoice date"),
    ("due_date", "due date"),
)

PROMPT = """The only file in this directory is invoice.pdf. Read that file and return the seven fields a bookkeeper would key from the page.

supplier is the letterhead name.
invoice_number is the invoice number.
invoice_date and due_date are calendar dates as YYYY-MM-DD. If the page has no due date, due_date is null.
subtotal, tax, and total are the printed amounts in decimal dollars with two places. Copy the printed total. Do not fix arithmetic.
Use null when a field is not on the page.

Do not open any other file or directory.
"""

OLLAMA_PROMPT = """This image is one supplier invoice. Return the seven fields a bookkeeper would key from the page.

supplier is the letterhead name.
invoice_number is the invoice number.
invoice_date and due_date are calendar dates as YYYY-MM-DD. If the page has no due date, due_date is null.
subtotal, tax, and total are the printed amounts in decimal dollars with two places. Copy the printed total. Do not fix arithmetic.
Use null when a field is not on the page.
"""

SCHEMA = json.dumps(
    {
        "type": "object",
        "additionalProperties": False,
        "required": list(FIELDS),
        "properties": {name: {"type": ["string", "null"]} for name in FIELDS},
    }
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


def field_right(name: str, got, expected) -> bool:
    if name in TEXT_FIELDS:
        return got == as_text(expected)
    if name in DATE_FIELDS:
        if got == "unparsed":
            return False
        return got == as_date(expected)
    if got == "unparsed":
        return False
    want = as_money(expected)
    if got is None or want is None:
        return got is None and want is None
    return got == want


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


def load_cli_payload(text: str) -> dict:
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty runner output")
    try:
        data = json.loads(stripped)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    found = None
    for line in stripped.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            found = obj
    if found is None:
        raise ValueError("runner output was not JSON")
    return found


def json_object(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed_value = json.loads(value)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed_value, dict):
            return parsed_value
    return None


def fields_from_payload(payload: dict) -> dict:
    for key in ("structured_output", "structuredOutput"):
        found = json_object(payload.get(key))
        if found is not None:
            return found
    for key in ("result", "text"):
        found = json_object(payload.get(key))
        if found is not None and any(name in found for name in FIELDS):
            return found
    raise ValueError("runner JSON had no fields")


def model_name(payload: dict) -> str | None:
    usage = payload.get("modelUsage") or payload.get("model_usage")
    if isinstance(usage, dict) and usage:
        def output_tokens(item) -> int:
            stats = item[1]
            if not isinstance(stats, dict):
                return 0
            return int(stats.get("outputTokens") or stats.get("output_tokens") or 0)

        return max(usage.items(), key=output_tokens)[0]
    model = payload.get("model")
    return model if isinstance(model, str) else None


def command(runner: str, work: Path, socket: Path) -> list[str]:
    if runner == "grok":
        return [
            "grok",
            "-p",
            PROMPT,
            "--cwd",
            str(work),
            "--output-format",
            "json",
            "--json-schema",
            SCHEMA,
            "--tools",
            "read_file",
            "--permission-mode",
            "dontAsk",
            "--no-subagents",
            "--disable-web-search",
            "--no-plan",
            "--max-turns",
            "4",
            "--leader-socket",
            str(socket),
        ]
    if runner == "claude":
        return [
            "claude",
            "-p",
            PROMPT,
            "--output-format",
            "json",
            "--json-schema",
            SCHEMA,
            "--tools",
            "Read",
            "--allowed-tools",
            "Read",
            "--permission-mode",
            "bypassPermissions",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
            "--add-dir",
            str(work),
        ]
    raise SystemExit(f"unknown runner {runner}")


def judged(row: dict, got_raw, model, seconds: float, error) -> dict:
    if got_raw is None:
        values = {name: None for name in FIELDS}
    else:
        values = parsed(got_raw)
    reasons = exception_reasons(values)
    rights = {name: field_right(name, values[name], row[name]) for name in FIELDS}
    return {
        "id": row["id"],
        "model": model,
        "seconds": seconds,
        "fields": {name: None if got_raw is None else got_raw.get(name) for name in FIELDS},
        "read": {name: show(values[name]) for name in FIELDS},
        "right": rights,
        "correct_fields": sum(rights.values()),
        "exception": bool(reasons),
        "exception_reasons": reasons,
        "label_exception": row["exception"],
        "error": error,
    }


def page_png(pdf: Path) -> bytes:
    import pymupdf

    doc = pymupdf.open(pdf)
    try:
        if doc.page_count < 1:
            raise RuntimeError(f"{pdf.name} has no pages")
        return doc[0].get_pixmap(dpi=150, alpha=False).tobytes("png")
    finally:
        doc.close()


def parse_fields_json(text: str) -> dict:
    stripped = text.strip()
    if not stripped:
        raise ValueError("ollama returned no fields")
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("ollama output was not JSON")
        data = json.loads(stripped[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("ollama output was not a field object")
    return {name: data.get(name) for name in FIELDS}


def ollama_read(pdf: Path, model: str) -> tuple[dict | None, str, str | None]:
    body = {
        "model": model,
        "stream": False,
        "think": False,
        "format": json.loads(SCHEMA),
        "options": {"temperature": 0},
        "messages": [
            {
                "role": "user",
                "content": OLLAMA_PROMPT,
                "images": [base64.b64encode(page_png(pdf)).decode("ascii")],
            }
        ],
    }
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except TimeoutError:
        return None, model, f"timed out after {OLLAMA_TIMEOUT}s"
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        return None, model, f"ollama request failed: {reason}"
    message = payload.get("message") if isinstance(payload, dict) else None
    content = message.get("content") if isinstance(message, dict) else ""
    used = payload.get("model") if isinstance(payload, dict) and isinstance(payload.get("model"), str) else model
    try:
        return parse_fields_json(content or ""), used, None
    except ValueError as exc:
        return None, used, str(exc)


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


def score_invoice(runner: str, row: dict, socket: Path, model: str | None = None) -> dict:
    source = ROOT / row["file"]
    started = time.perf_counter()
    if runner == "docling":
        try:
            got_raw, used, error = docling_read(source)
        except Exception as exc:
            return judged(row, None, "docling", round(time.perf_counter() - started, 1), str(exc))
        return judged(row, got_raw, used, round(time.perf_counter() - started, 1), error)
    if runner == "ollama":
        try:
            got_raw, used, error = ollama_read(source, model or OLLAMA_MODEL)
        except Exception as exc:
            return judged(row, None, model, round(time.perf_counter() - started, 1), str(exc))
        return judged(row, got_raw, used, round(time.perf_counter() - started, 1), error)
    work = Path(tempfile.mkdtemp(prefix=f"iap-{row['id']}-"))
    try:
        shutil.copy(source, work / "invoice.pdf")
        proc = subprocess.run(
            command(runner, work, socket),
            cwd=work,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CALL_TIMEOUT,
        )
        seconds = round(time.perf_counter() - started, 1)
        raw = proc.stdout or ""
        contaminated = "labels.json" in raw
        error = None
        got_raw = None
        seen = None
        payload = None
        try:
            payload = load_cli_payload(raw)
        except ValueError:
            payload = None
        if payload is not None:
            seen = model_name(payload)
        denials = payload.get("permission_denials") if payload is not None else None
        if denials:
            error = "tool permission denied"
        elif payload is not None and payload.get("is_error"):
            error = str(payload.get("result") or "runner error")
        elif proc.returncode != 0:
            error = (proc.stderr or raw or f"exit {proc.returncode}")[-500:]
        elif payload is None:
            error = "runner output was not JSON"
        else:
            try:
                got_raw = fields_from_payload(payload)
            except ValueError as exc:
                error = str(exc)
        if contaminated:
            error = "runner output mentioned labels.json"
            got_raw = None
        return judged(row, got_raw, seen, seconds, error)
    except subprocess.TimeoutExpired:
        return judged(row, None, None, CALL_TIMEOUT, f"timed out after {CALL_TIMEOUT}s")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def summarize(runner: str, invoices: list[dict], elapsed: float) -> dict:
    correct = sum(row["correct_fields"] for row in invoices)
    total = len(FIELDS) * len(invoices)
    clear = sum(1 for row in invoices if not row["exception"])
    return {
        "runner": runner,
        "date": "2026-10-22",
        "invoices": len(invoices),
        "field_accuracy": f"{correct}/{total}",
        "straight_through": f"{clear}/{len(invoices)}",
        "time_per_invoice_seconds": round(elapsed / len(invoices), 1),
        "elapsed_seconds": round(elapsed, 1),
        "time_includes": "starting the CLI and the model call for each invoice, one at a time",
        "models": sorted({row["model"] for row in invoices if row.get("model")}),
        "results": invoices,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", choices=("grok", "claude", "ollama", "docling"), required=True)
    parser.add_argument(
        "--model",
        default=OLLAMA_MODEL,
        help="Ollama model name. Ignored for grok, claude, and docling.",
    )
    parser.add_argument("--only", help="Score one fixture id, such as 01-northline")
    parser.add_argument(
        "--labels",
        type=Path,
        default=LABELS,
        help="Label file. The default set keeps its run name. Another file gets its own.",
    )
    args = parser.parse_args()
    labels_path = args.labels if args.labels.is_absolute() else ROOT / args.labels
    rows = json.loads(labels_path.read_text(encoding="utf-8"))["invoices"]
    if args.only:
        rows = [row for row in rows if row["id"] == args.only]
        if not rows:
            raise SystemExit(f"no fixture {args.only}")
    socket = Path(tempfile.gettempdir()) / f"iap-{args.runner}-{os.getpid()}.sock"
    started = time.perf_counter()
    results = []
    for row in rows:
        print(f"START {args.runner} {row['id']}", flush=True)
        item = score_invoice(args.runner, row, socket, args.model)
        results.append(item)
        print(
            f"DONE {args.runner} {row['id']} {item['correct_fields']}/7 "
            f"exception={item['exception']} {item['seconds']}s",
            flush=True,
        )
    elapsed = time.perf_counter() - started
    report = summarize(args.runner, results, elapsed)
    if args.runner == "ollama":
        report["date"] = date.today().isoformat()
        report["time_includes"] = (
            "rendering the page and the local Ollama call for each invoice, one at a time"
        )
    if args.runner == "docling":
        report["date"] = date.today().isoformat()
        report["time_includes"] = "the local Docling conversion for each invoice, one at a time"
    if args.only:
        print(json.dumps(report, indent=2))
        return
    RUNS.mkdir(exist_ok=True)
    if args.runner == "ollama" and labels_path.resolve() == LABELS.resolve():
        slug = args.model.replace(":", "-").replace("/", "-")
        dest = RUNS / f"{date.today().isoformat()}-{slug}.json"
    elif args.runner == "docling" and labels_path.resolve() == LABELS.resolve():
        dest = RUNS / f"{date.today().isoformat()}-docling.json"
    elif labels_path.resolve() == LABELS.resolve():
        dest = RUNS / f"2026-10-22-{args.runner}.json"
    else:
        stem = labels_path.parent.name if labels_path.stem == "labels" else labels_path.stem
        dest = RUNS / f"{date.today().isoformat()}-{stem}-{args.runner}.json"
    dest.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"SCORE {args.runner} field_accuracy={report['field_accuracy']} "
        f"straight_through={report['straight_through']} "
        f"time_per_invoice_seconds={report['time_per_invoice_seconds']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
