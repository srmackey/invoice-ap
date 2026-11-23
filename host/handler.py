"""The hosted reader: one invoice in, the seven fields and the exception out.

Runs on AWS Lambda behind a function URL with AWS_IAM auth. The request body
is one PDF. The function reads it with read_invoice.py, the same reader the
command, the page, and the scorer use, through Textract AnalyzeExpense.

Response (JSON): the seven fields, exception, exception_reasons, currency
(the ISO code the invoice names, or null when it names none), printed (the
printed text of any date or amount that did not parse), error, runner,
pages, and seconds. The printed text is a field value, so it stays out of the
log line like the rest.

  200  the read finished (an exception is still a 200: it is the answer)
  400  the body is not a PDF
  405  not a POST
  502  Textract failed; error says why

Tracing: the function runs under AWS's OpenTelemetry Lambda layer with X-Ray
active tracing, so each sampled read is one trace from the function URL
through Textract to the seven fields. The log line carries the trace id.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import time
from pathlib import Path

from read_invoice import read_invoice

PDF_MAGIC = b"%PDF"


def reply(status: int, payload: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }


def body_bytes(event: dict) -> bytes:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        return base64.b64decode(body)
    return body.encode("latin-1")


def trace_id() -> str | None:
    """The X-Ray trace id of this invocation, from the header Lambda sets when
    tracing is on: "Root=1-...;Parent=...;Sampled=1"."""
    for part in os.environ.get("_X_AMZN_TRACE_ID", "").split(";"):
        key, _, value = part.partition("=")
        if key == "Root" and value:
            return value
    return None


def handler(event: dict, context) -> dict:
    method = ((event.get("requestContext") or {}).get("http") or {}).get("method")
    if method != "POST":
        return reply(405, {"error": "POST one PDF as the request body"})
    data = body_bytes(event)
    if not data.startswith(PDF_MAGIC):
        return reply(400, {"error": "the request body is not a PDF"})
    started = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "invoice.pdf"
        pdf.write_bytes(data)
        result = read_invoice(pdf)
    result["seconds"] = round(time.perf_counter() - started, 2)
    request_id = getattr(context, "aws_request_id", None)
    # One log line per read. Field values stay out of the log.
    print(
        json.dumps(
            {
                "read": request_id,
                "trace": trace_id(),
                "bytes": len(data),
                "pages": result["pages"],
                "seconds": result["seconds"],
                "exception": result["exception"],
                "exception_reasons": result["exception_reasons"],
                "error": result["error"],
            }
        )
    )
    return reply(502 if result["error"] else 200, result)
