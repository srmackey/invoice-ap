"""Store one trace of a hosted read under sandbox/traces/.

The reader's log line carries the trace id of each read ("trace": "1-...").
This account sends traces to CloudWatch Logs (X-Ray Transaction Search), so
the spans are records in the log group aws/spans, and X-Ray's BatchGetTraces
returns only the small share of traces it indexes. This script reads the
spans from that log group, keeps the finished copy of each span, replaces the
account id with <account> and any access key id with <redacted>, and writes
them in start order.

Run from the checkout with sandbox/.venv:

    python sandbox/get_trace.py 1-6b03bc70-22469f6173b462ec0c57d302 --profile <profile> --name hard-scan-08

The trace id's second part is the start time in hex seconds, which bounds the
search. Spans carry no field values; that is the reader's rule (DESIGN.md,
Host), and this script does not check it.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

SANDBOX = Path(__file__).resolve().parent
LOG_GROUP = "aws/spans"


def otel_id(trace_id: str) -> str:
    """X-Ray's "1-6b03bc70-2246..." is OpenTelemetry's "6b03bc702246..."."""
    parts = trace_id.split("-")
    if len(parts) != 3 or parts[0] != "1" or len(parts[1]) != 8 or len(parts[2]) != 24:
        raise ValueError(f"not an X-Ray trace id: {trace_id}")
    return parts[1] + parts[2]


def started(trace_id: str) -> int:
    """The trace's start, in epoch seconds, from its second part."""
    return int(trace_id.split("-")[1], 16)


def finished_spans(records: list[dict]) -> list[dict]:
    """One span per spanId, preferring the copy that has an end time. Lambda
    sends a span when it starts and again when it ends."""
    best: dict[str, dict] = {}
    for span in records:
        key = span.get("spanId")
        if key not in best or (best[key].get("endTimeUnixNano") is None and span.get("endTimeUnixNano") is not None):
            best[key] = span
    return sorted(best.values(), key=lambda span: span.get("startTimeUnixNano") or 0)


def redacted(span: dict) -> dict:
    """The layer's botocore instrumentation records the role's temporary
    access key id on each AWS call. It is not the secret, but it stays out of
    the stored copy."""
    attributes = span.get("attributes") or {}
    for key in attributes:
        if "access_key" in key:
            attributes[key] = "<redacted>"
    return span


def milliseconds(span: dict) -> float | None:
    start, end = span.get("startTimeUnixNano"), span.get("endTimeUnixNano")
    return None if start is None or end is None else round((end - start) / 1e6, 1)


def outline(spans: list[dict]) -> list[str]:
    """Each span as an indented line under its parent."""
    children: dict[str | None, list[dict]] = {}
    ids = {span["spanId"] for span in spans}
    for span in spans:
        parent = span.get("parentSpanId")
        children.setdefault(parent if parent in ids else None, []).append(span)
    lines: list[str] = []

    def walk(parent: str | None, depth: int) -> None:
        for span in children.get(parent, []):
            took = milliseconds(span)
            lines.append(f"{'  ' * depth}{span.get('name')} ({span.get('kind')}, {took} ms)")
            walk(span["spanId"], depth + 1)

    walk(None, 0)
    return lines


def fetch(trace_id: str, profile: str | None, region: str) -> tuple[list[dict], str]:
    import boto3

    session = boto3.Session(profile_name=profile, region_name=region)
    account = session.client("sts").get_caller_identity()["Account"]
    logs = session.client("logs")
    begin = started(trace_id)
    params = {
        "logGroupName": LOG_GROUP,
        "startTime": (begin - 60) * 1000,
        "endTime": (begin + 15 * 60) * 1000,
        "filterPattern": f'{{ $.traceId = "{otel_id(trace_id)}" }}',
    }
    records = []
    for page in logs.get_paginator("filter_log_events").paginate(**params):
        records.extend(json.loads(event["message"]) for event in page["events"])
    return records, account


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("trace_id", help='the "trace" value from the reader\'s log line')
    parser.add_argument("--profile", default=None)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--name", required=True, help="what was read, for the file name")
    args = parser.parse_args()

    records, account = fetch(args.trace_id, args.profile, args.region)
    spans = [redacted(span) for span in finished_spans(records)]
    if not spans:
        raise SystemExit(f"no spans for {args.trace_id} in {LOG_GROUP}; spans can take a minute to arrive")
    stored = {
        "trace": args.trace_id,
        "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": f"CloudWatch Logs group {LOG_GROUP} (X-Ray Transaction Search), region {args.region}",
        "account": "replaced with <account>; access key ids replaced with <redacted>",
        "outline": outline(spans),
        "spans": spans,
    }
    text = json.dumps(stored, indent=2, ensure_ascii=False).replace(account, "<account>")
    out = SANDBOX / "traces" / f"{date.today().isoformat()}-{args.name}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8")
    print(f"wrote {out.relative_to(SANDBOX.parent)}: {len(spans)} spans from {len(records)} records")
    print("\n".join(stored["outline"]))


if __name__ == "__main__":
    main()
