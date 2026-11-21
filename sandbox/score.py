"""Score the sandbox invoices with Grok, Claude, Ollama, local Docling,
Textract from this machine, or the hosted reader.

The runner returns the seven fields. This script applies the exception rule
and the four scores in ../AGENTS.md. Each run file also lists
silent errors: invoices that raised no exception but read a field wrong. Each call sees only that invoice, so
the runner cannot open labels.json. Ollama receives a rendered page, not a
directory. Textract and Docling reading, and the exception rule, live in
../read_invoice.py, the same code the command, the page, and the host use.

Usage:
  python score.py --runner grok
  python score.py --runner claude
  python score.py --runner grok --only 01-northline
  python score.py --runner claude --labels hard/labels.json
  python score.py --runner ollama
  python score.py --runner ollama --model qwen3-vl:8b --only 01-northline
  python score.py --runner docling --only 01-northline
  python score.py --runner docling --gate
  python score.py --runner docling --labels hard/labels.json --gate
  python score.py --runner textract --profile <profile> --labels hard/labels.json
  python score.py --runner hosted --url <reader url> --profile <profile> --labels holdout/labels.json

The textract runner needs boto3 (sandbox/.venv) and a signed-in AWS profile.
It records pages and list-price cost per invoice in the run file.

The hosted runner sends each PDF to the deployed function URL, signed with
host/call.py, so what is scored is the function that is running. The URL can
also come from INVOICE_AP_READER_URL. The profile needs lambda:InvokeFunctionUrl
and lambda:InvokeFunction on the function.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHECKOUT = ROOT.parent
for path in (CHECKOUT, CHECKOUT / "host"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import call as host_call  # noqa: E402

from read_invoice import (
    DATE_FIELDS,
    FIELDS,
    TEXT_FIELDS,
    as_date,
    as_money,
    as_text,
    docling_read,
    exception_reasons,
    parsed,
    read_fields,
    show,
    textract_client,
)

LABELS = ROOT / "labels.json"
RUNS = ROOT / "runs"
CALL_TIMEOUT = 180
OLLAMA_TIMEOUT = 600
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_MODEL = "qwen3-vl:8b"

# AnalyzeExpense list price, first million pages a month, read from
# https://aws.amazon.com/textract/pricing/ on 2026-11-20.
TEXTRACT_PRICE_PER_PAGE = 0.01

# Lambda, read from https://aws.amazon.com/lambda/pricing/ on 2026-11-21.
# The page text gave $0.20 per million requests and $0.0000166667 per GB-second
# in its examples, which is the x86 rate. The function runs on Arm, which is
# cheaper, and that rate did not come through as text, so the compute figure
# is an upper bound. Billed duration is not returned to the caller; the
# function's own read time stands in for it.
LAMBDA_PRICE_PER_REQUEST = 0.20 / 1_000_000
LAMBDA_PRICE_PER_GB_SECOND_CEILING = 0.0000166667
LAMBDA_MEMORY_GB = 256 / 1024

PROMPT = """The only file in this directory is invoice.pdf. Read that file and return the seven fields a bookkeeper would key from the page.

supplier is the letterhead name.
invoice_number is the invoice number.
invoice_date and due_date are calendar dates as YYYY-MM-DD. If the page prints payment terms but no due date, due_date is the invoice date plus the terms' days: "Due on receipt" is the invoice date and "2/10 Net 30" is 30 days. If the page has neither, or the terms cannot be computed (such as "Net 30 EOM"), due_date is null.
subtotal, tax, and total are the printed amounts in decimal dollars with two places. Copy the printed total. Do not fix arithmetic.
Use null when a field is not on the page.

Do not open any other file or directory.
"""

OLLAMA_PROMPT = """This image is one supplier invoice. Return the seven fields a bookkeeper would key from the page.

supplier is the letterhead name.
invoice_number is the invoice number.
invoice_date and due_date are calendar dates as YYYY-MM-DD. If the page prints payment terms but no due date, due_date is the invoice date plus the terms' days: "Due on receipt" is the invoice date and "2/10 Net 30" is 30 days. If the page has neither, or the terms cannot be computed (such as "Net 30 EOM"), due_date is null.
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
    rights = {name: field_right(name, values[name], row.get(name)) for name in labeled(row)}
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
        "label_exception": row.get("exception"),
        "error": error,
    }


def labeled(row: dict) -> tuple[str, ...]:
    """The fields this invoice is judged on: all seven, unless its label row
    lists fewer. A field a set does not label is not judged, right or wrong."""
    names = row.get("labeled")
    if names is None:
        return FIELDS
    unknown = set(names) - set(FIELDS)
    if unknown:
        raise ValueError(f"{row['id']} labels unknown fields {sorted(unknown)}")
    return tuple(name for name in FIELDS if name in names)


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


def hosted_read(source: Path, url: str, credentials) -> tuple[dict | None, str, str | None, dict]:
    """One signed call to the function URL: (fields, runner, error, host facts).

    The function returns the seven already read. Scoring parses them again,
    which leaves an ISO date or a two-place amount unchanged, so the exception
    is recomputed from what the host returned and checked against the host's.
    """
    try:
        status, body = host_call.call_reader(url, source, credentials=credentials)
    except Exception as exc:
        return None, "hosted", f"call failed: {exc}", {"status": None, "pages": 0}
    facts = {
        "status": status,
        "pages": body.get("pages") or 0,
        "host_seconds": body.get("seconds"),
        "host_exception_reasons": body.get("exception_reasons"),
    }
    used = body.get("runner") or "hosted"
    if status != 200:
        return None, used, body.get("error") or f"HTTP {status}", facts
    return {name: body.get(name) for name in FIELDS}, used, body.get("error"), facts


def score_invoice(
    runner: str, row: dict, socket: Path, model: str | None = None, client=None, host=None
) -> dict:
    source = ROOT / row["file"]
    started = time.perf_counter()
    if runner == "hosted":
        url, credentials = host
        got_raw, used, error, facts = hosted_read(source, url, credentials)
        item = judged(row, got_raw, used, round(time.perf_counter() - started, 1), error)
        item.update(facts)
        if got_raw is not None and facts["host_exception_reasons"] != item["exception_reasons"]:
            item["host_disagrees"] = True
        return item
    if runner == "textract":
        got_raw, used, error, pages = read_fields(source, "textract", client)
        item = judged(row, got_raw, used, round(time.perf_counter() - started, 1), error)
        item["pages"] = pages
        return item
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


def gate_misses(invoices: list[dict]) -> list[str]:
    """Ids that would fail a build: a wrong field, or an exception that
    does not match the label. An empty run fails too."""
    if not invoices:
        return ["no invoices"]
    misses = []
    for row in invoices:
        parts = []
        width = scored_fields(row)
        if row["correct_fields"] != width:
            parts.append(f"{row['correct_fields']}/{width}")
        if row["label_exception"] is not None and bool(row["exception"]) != bool(row["label_exception"]):
            parts.append(
                f"exception={bool(row['exception'])} label={bool(row['label_exception'])}"
            )
        if parts:
            misses.append(f"{row['id']} {', '.join(parts)}")
    return misses


def enforce_gate(report: dict) -> None:
    misses = gate_misses(report["results"])
    if not misses:
        print(
            f"GATE pass field_accuracy={report['field_accuracy']} "
            f"straight_through={report['straight_through']}",
            flush=True,
        )
        return
    for line in misses:
        print(f"GATE miss {line}", flush=True)
    raise SystemExit(1)


def scored_fields(row: dict) -> int:
    """How many fields this invoice was scored on. Seven unless a set labels fewer."""
    right = row.get("right")
    return len(FIELDS) if right is None else len(right)


def invoice_scores(invoices: list[dict]) -> tuple[str, list[str]]:
    """Invoice accuracy (every scored field right) and the silent errors.

    A silent error raised no exception and still read a field wrong, so it
    would have gone through without anyone looking at it.
    """
    whole = sum(1 for row in invoices if row["correct_fields"] == scored_fields(row))
    silent = [
        row["id"]
        for row in invoices
        if not row["exception"] and row["correct_fields"] < scored_fields(row)
    ]
    return f"{whole}/{len(invoices)}", silent


def summarize(runner: str, invoices: list[dict], elapsed: float) -> dict:
    correct = sum(row["correct_fields"] for row in invoices)
    total = sum(scored_fields(row) for row in invoices)
    clear = sum(1 for row in invoices if not row["exception"])
    invoice_accuracy, silent = invoice_scores(invoices)
    return {
        "runner": runner,
        "date": "2026-10-22",
        "invoices": len(invoices),
        "field_accuracy": f"{correct}/{total}",
        "straight_through": f"{clear}/{len(invoices)}",
        "invoice_accuracy": invoice_accuracy,
        "silent_errors": silent,
        "time_per_invoice_seconds": round(elapsed / len(invoices), 1),
        "elapsed_seconds": round(elapsed, 1),
        "time_includes": "starting the CLI and the model call for each invoice, one at a time",
        "models": sorted({row["model"] for row in invoices if row.get("model")}),
        "results": invoices,
    }


def add_hosted_facts(report: dict, results: list[dict], url: str) -> None:
    """What a hosted run adds to the four scores: where it ran, what it cost,
    and whether the host's exception matched the one scoring recomputed."""
    pages = sum(row.get("pages", 0) for row in results)
    host_seconds = sum(row.get("host_seconds") or 0 for row in results)
    calls = len(results)
    textract = pages * TEXTRACT_PRICE_PER_PAGE
    lambda_ceiling = calls * LAMBDA_PRICE_PER_REQUEST + (
        host_seconds * LAMBDA_MEMORY_GB * LAMBDA_PRICE_PER_GB_SECOND_CEILING
    )
    report["date"] = date.today().isoformat()
    report["url_region"] = host_call.region_of(url)
    report["time_includes"] = (
        "a signed HTTPS call from this machine to the function URL for each invoice, one at a "
        "time: upload, the function's read through Textract, and the reply"
    )
    report["host_seconds_per_invoice"] = round(host_seconds / calls, 2)
    report["pages"] = pages
    report["price_per_page_usd"] = TEXTRACT_PRICE_PER_PAGE
    report["lambda_cost_ceiling_usd"] = round(lambda_ceiling, 6)
    report["cost_usd"] = round(textract + lambda_ceiling, 4)
    report["cost_per_invoice_usd"] = round((textract + lambda_ceiling) / calls, 4)
    report["cost_note"] = (
        "Textract at list price per page, plus Lambda requests and compute at the x86 rate, an "
        "upper bound for this Arm function, using the function's read time for duration."
    )
    report["host_disagrees"] = [row["id"] for row in results if row.get("host_disagrees")]
    report["http_errors"] = [
        f"{row['id']} {row.get('status')}" for row in results if row.get("status") != 200
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--runner",
        choices=("grok", "claude", "ollama", "docling", "textract", "hosted"),
        required=True,
    )
    parser.add_argument("--profile", help="AWS profile for the textract and hosted runners.")
    parser.add_argument(
        "--url",
        default=os.environ.get("INVOICE_AP_READER_URL"),
        help="Function URL for the hosted runner. Defaults to INVOICE_AP_READER_URL.",
    )
    parser.add_argument("--region", default="us-east-1", help="AWS region for the textract runner.")
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
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Exit 1 when a field is wrong or the exception does not match the label.",
    )
    args = parser.parse_args()
    labels_path = args.labels if args.labels.is_absolute() else ROOT / args.labels
    label_set = json.loads(labels_path.read_text(encoding="utf-8"))
    rows = label_set["invoices"]
    if args.only:
        rows = [row for row in rows if row["id"] == args.only]
        if not rows:
            raise SystemExit(f"no fixture {args.only}")
    socket = Path(tempfile.gettempdir()) / f"iap-{args.runner}-{os.getpid()}.sock"
    client = None
    host = None
    if args.runner == "textract":
        client = textract_client(args.profile, args.region)
    if args.runner == "hosted":
        if not args.url:
            raise SystemExit("the hosted runner needs --url or INVOICE_AP_READER_URL")
        host = (args.url, host_call.session_credentials(args.url, args.profile))
    started = time.perf_counter()
    results = []
    for row in rows:
        print(f"START {args.runner} {row['id']}", flush=True)
        item = score_invoice(args.runner, row, socket, args.model, client, host)
        results.append(item)
        print(
            f"DONE {args.runner} {row['id']} {item['correct_fields']}/{scored_fields(item)} "
            f"exception={item['exception']} {item['seconds']}s",
            flush=True,
        )
    elapsed = time.perf_counter() - started
    report = summarize(args.runner, results, elapsed)
    report["labeled_fields"] = label_set.get("fields", list(FIELDS))
    if label_set.get("exception_labeled") is False:
        report["exception_labeled"] = False
        report["straight_through_note"] = (
            "This set does not label supplier or subtotal, so the exception rule cannot be "
            "judged against it. The straight-through rate is recorded, not compared."
        )
    if args.runner == "ollama":
        report["date"] = date.today().isoformat()
        report["time_includes"] = (
            "rendering the page and the local Ollama call for each invoice, one at a time"
        )
    if args.runner == "docling":
        report["date"] = date.today().isoformat()
        report["time_includes"] = "the local Docling conversion for each invoice, one at a time"
    if args.runner == "textract":
        pages = sum(row.get("pages", 0) for row in results)
        report["date"] = date.today().isoformat()
        report["region"] = args.region
        report["time_includes"] = (
            "one synchronous AnalyzeExpense call from this machine for each invoice, one at a time"
        )
        report["pages"] = pages
        report["price_per_page_usd"] = TEXTRACT_PRICE_PER_PAGE
        report["cost_usd"] = round(pages * TEXTRACT_PRICE_PER_PAGE, 4)
        report["cost_per_invoice_usd"] = round(pages * TEXTRACT_PRICE_PER_PAGE / len(results), 4)
    if args.runner == "hosted":
        add_hosted_facts(report, results, args.url)
    if args.only:
        print(json.dumps(report, indent=2))
        if args.gate:
            enforce_gate(report)
        return
    RUNS.mkdir(exist_ok=True)
    if args.runner in ("textract", "hosted"):
        stem = "" if labels_path.resolve() == LABELS.resolve() else (
            (labels_path.parent.name if labels_path.stem == "labels" else labels_path.stem) + "-"
        )
        candidate = RUNS / f"{date.today().isoformat()}-{stem}{args.runner}.json"
        if all(row["error"] for row in results):
            raise SystemExit(f"every call failed, no run written: {results[0]['error']}")
        if candidate.exists():
            raise SystemExit(f"{candidate.name} exists. A scored run is never overwritten.")
    if args.runner == "ollama" and labels_path.resolve() == LABELS.resolve():
        slug = args.model.replace(":", "-").replace("/", "-")
        dest = RUNS / f"{date.today().isoformat()}-{slug}.json"
    elif args.runner in ("docling", "textract", "hosted") and labels_path.resolve() == LABELS.resolve():
        dest = RUNS / f"{date.today().isoformat()}-{args.runner}.json"
    elif labels_path.resolve() == LABELS.resolve():
        dest = RUNS / f"2026-10-22-{args.runner}.json"
    else:
        stem = labels_path.parent.name if labels_path.stem == "labels" else labels_path.stem
        dest = RUNS / f"{date.today().isoformat()}-{stem}-{args.runner}.json"
    dest.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"SCORE {args.runner} field_accuracy={report['field_accuracy']} "
        f"straight_through={report['straight_through']} "
        f"invoice_accuracy={report['invoice_accuracy']} "
        f"silent_errors={len(report['silent_errors'])} "
        f"time_per_invoice_seconds={report['time_per_invoice_seconds']}",
        flush=True,
    )
    if args.gate:
        enforce_gate(report)


if __name__ == "__main__":
    main()
