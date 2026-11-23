"""The hosted handler and the caller, without AWS.

Run from the checkout:
  python sandbox/test_host.py
"""

from __future__ import annotations

import base64
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

CHECKOUT = Path(__file__).resolve().parent.parent
for path in (CHECKOUT, CHECKOUT / "host", CHECKOUT / "sandbox"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import call  # noqa: E402
import handler  # noqa: E402

PDF = b"%PDF-1.4 stand-in"
READ = {
    "supplier": "Northline Supply Co.",
    "invoice_number": "NL-10442",
    "invoice_date": "2026-02-03",
    "due_date": "2026-03-05",
    "subtotal": "1260.00",
    "tax": "100.80",
    "total": "1360.80",
    "exception": False,
    "exception_reasons": [],
    "error": None,
    "runner": "textract-analyze-expense",
    "pages": 1,
}


def event(body: bytes, method: str = "POST", encoded: bool = True) -> dict:
    return {
        "requestContext": {"http": {"method": method}},
        "isBase64Encoded": encoded,
        "body": base64.b64encode(body).decode("ascii") if encoded else body.decode("latin-1"),
    }


CONTEXT = SimpleNamespace(aws_request_id="req-1")


class Handler(unittest.TestCase):
    def call(self, evt, result=None):
        seen = {}

        def fake_read(pdf):
            seen["bytes"] = Path(pdf).read_bytes()
            return dict(result or READ)

        with mock.patch.object(handler, "read_invoice", side_effect=fake_read), mock.patch("builtins.print"):
            out = handler.handler(evt, CONTEXT)
        return out, json.loads(out["body"]), seen

    def test_a_pdf_gets_the_seven_fields(self):
        out, body, seen = self.call(event(PDF))
        self.assertEqual(out["statusCode"], 200)
        self.assertEqual(seen["bytes"], PDF)
        self.assertEqual(body["invoice_number"], "NL-10442")
        self.assertIs(body["exception"], False)
        self.assertIn("seconds", body)

    def test_an_exception_is_still_an_answer(self):
        flagged = dict(READ, due_date=None, exception=True, exception_reasons=["missing_due_date"])
        out, body, _ = self.call(event(PDF), flagged)
        self.assertEqual(out["statusCode"], 200)
        self.assertEqual(body["exception_reasons"], ["missing_due_date"])

    def test_a_textract_failure_is_a_502_with_the_error(self):
        failed = dict(READ, error="textract request failed: UnsupportedDocumentException")
        out, body, _ = self.call(event(PDF), failed)
        self.assertEqual(out["statusCode"], 502)
        self.assertIn("UnsupportedDocumentException", body["error"])

    def test_a_plain_body_is_read_too(self):
        out, _body, seen = self.call(event(PDF, encoded=False))
        self.assertEqual(out["statusCode"], 200)
        self.assertEqual(seen["bytes"], PDF)

    def test_not_a_pdf_is_refused(self):
        out, body, seen = self.call(event(b"hello"))
        self.assertEqual(out["statusCode"], 400)
        self.assertEqual(seen, {})

    def test_get_is_refused(self):
        out, _body, seen = self.call(event(PDF, method="GET"))
        self.assertEqual(out["statusCode"], 405)
        self.assertEqual(seen, {})

    def test_the_log_line_carries_no_field_values(self):
        with mock.patch.object(handler, "read_invoice", return_value=dict(READ)), mock.patch("builtins.print") as printed:
            handler.handler(event(PDF), CONTEXT)
        line = printed.call_args[0][0]
        self.assertNotIn("Northline", line)
        self.assertNotIn("1360.80", line)
        self.assertEqual(json.loads(line)["read"], "req-1")

    def test_the_log_line_carries_the_trace_id(self):
        header = "Root=1-674e1a2b-0123456789abcdef01234567;Parent=53995c3f42cd8ad8;Sampled=1"
        with (
            mock.patch.dict("os.environ", {"_X_AMZN_TRACE_ID": header}),
            mock.patch.object(handler, "read_invoice", return_value=dict(READ)),
            mock.patch("builtins.print") as printed,
        ):
            handler.handler(event(PDF), CONTEXT)
        self.assertEqual(json.loads(printed.call_args[0][0])["trace"], "1-674e1a2b-0123456789abcdef01234567")

    def test_no_trace_header_logs_no_trace(self):
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            mock.patch.object(handler, "read_invoice", return_value=dict(READ)),
            mock.patch("builtins.print") as printed,
        ):
            handler.handler(event(PDF), CONTEXT)
        self.assertIsNone(json.loads(printed.call_args[0][0])["trace"])


class Spans(unittest.TestCase):
    """The reader's two spans, with a stand-in tracer in place of OpenTelemetry."""

    RAW = {
        "supplier": "Northline Supply Co.",
        "invoice_number": "NL-10442",
        "invoice_date": "2026-02-03",
        "due_date": "2026-03-05",
        "subtotal": "1260.00",
        "tax": "100.80",
        "total": "1360.80",
    }

    def read(self, raw, error=None):
        import contextlib

        import read_invoice

        spans = []

        class Span:
            def __init__(self, name):
                self.name, self.attributes = name, {}
                spans.append(self)

            def set_attribute(self, key, value):
                self.attributes[key] = value

        class Tracer:
            @contextlib.contextmanager
            def start_as_current_span(self, name):
                yield Span(name)

        fake = SimpleNamespace(get_tracer=lambda _name: Tracer())
        returned = (raw, "textract-analyze-expense", error, 1 if raw else 0)
        with (
            mock.patch.object(read_invoice, "otel_trace", fake),
            mock.patch.object(read_invoice, "read_fields", return_value=returned),
        ):
            out = read_invoice.read_invoice(Path("unused.pdf"))
        return out, {span.name: span.attributes for span in spans}

    def test_a_read_makes_a_runner_span_and_a_mapping_span(self):
        out, spans = self.read(self.RAW)
        self.assertEqual(list(spans), ["runner", "mapping"])
        self.assertEqual(spans["runner"]["invoice_ap.runner"], "textract")
        self.assertEqual(spans["runner"]["invoice_ap.pages"], 1)
        self.assertIs(spans["runner"]["invoice_ap.error"], False)
        self.assertEqual(spans["mapping"]["invoice_ap.fields_read"], 7)
        self.assertIs(spans["mapping"]["invoice_ap.exception"], out["exception"])

    def test_an_exception_names_its_reasons_on_the_span(self):
        _out, spans = self.read(dict(self.RAW, due_date=None))
        self.assertEqual(spans["mapping"]["invoice_ap.fields_read"], 6)
        self.assertIn("missing_due_date", spans["mapping"]["invoice_ap.exception_reasons"])

    def test_a_runner_failure_is_marked(self):
        _out, spans = self.read(None, error="textract request failed: UnsupportedDocumentException")
        self.assertIs(spans["runner"]["invoice_ap.error"], True)
        self.assertEqual(spans["mapping"]["invoice_ap.fields_read"], 0)

    def test_no_span_carries_a_field_value(self):
        _out, spans = self.read(self.RAW)
        text = json.dumps(spans)
        for value in self.RAW.values():
            self.assertNotIn(value, text)
        self.assertNotIn("2026-03-05", text)

    def test_without_opentelemetry_the_read_is_unchanged(self):
        import read_invoice

        returned = (self.RAW, "textract-analyze-expense", None, 1)
        with (
            mock.patch.object(read_invoice, "otel_trace", None),
            mock.patch.object(read_invoice, "read_fields", return_value=returned),
        ):
            out = read_invoice.read_invoice(Path("unused.pdf"))
        self.assertEqual(out["invoice_number"], "NL-10442")
        self.assertEqual(out["total"], "1360.80")


class Caller(unittest.TestCase):
    def test_region_comes_from_the_url(self):
        self.assertEqual(call.region_of("https://abc123.lambda-url.us-east-1.on.aws/"), "us-east-1")

    def test_a_non_function_url_is_refused(self):
        with self.assertRaises(ValueError):
            call.region_of("https://example.com/")

    def test_the_request_is_signed_for_lambda(self):
        from botocore.credentials import Credentials

        creds = Credentials("AKIDEXAMPLE", "secret").get_frozen_credentials()
        req = call.signed_request("https://abc123.lambda-url.us-east-1.on.aws/", PDF, creds, "us-east-1")
        auth = req.get_header("Authorization")
        self.assertIn("/us-east-1/lambda/aws4_request", auth)
        self.assertEqual(req.data, PDF)
        self.assertEqual(req.get_method(), "POST")


class ScoreHosted(unittest.TestCase):
    """score.py's hosted runner, with the signed call stood in."""

    ROW = {
        "id": "01-northline",
        "file": "fixtures/01-northline.pdf",
        "supplier": "Northline Supply Co.",
        "invoice_number": "NL-10442",
        "invoice_date": "2026-02-03",
        "due_date": "2026-03-05",
        "subtotal": "1260.00",
        "tax": "100.80",
        "total": "1360.80",
        "exception": False,
    }
    HOST = ("https://abc123.lambda-url.us-east-1.on.aws/", object())

    def score(self, status, body):
        import score

        with mock.patch.object(score.host_call, "call_reader", return_value=(status, body)) as fake:
            item = score.score_invoice("hosted", self.ROW, Path("unused"), host=self.HOST)
        self.assertIs(fake.call_args.kwargs["credentials"], self.HOST[1])
        return item

    def test_the_host_read_is_scored(self):
        item = self.score(200, {**READ, "seconds": 2.9})
        self.assertEqual(item["correct_fields"], 7)
        self.assertIs(item["exception"], False)
        self.assertEqual(item["pages"], 1)
        self.assertEqual(item["host_seconds"], 2.9)
        self.assertNotIn("host_disagrees", item)

    def test_a_502_is_an_error_with_no_fields(self):
        item = self.score(502, {"error": "UnsupportedDocumentException", "pages": 0})
        self.assertEqual(item["error"], "UnsupportedDocumentException")
        self.assertEqual(item["correct_fields"], 0)
        self.assertEqual(item["status"], 502)

    def test_a_host_exception_that_scoring_does_not_reproduce_is_flagged(self):
        item = self.score(200, {**READ, "exception": True, "exception_reasons": ["total_mismatch"]})
        self.assertIs(item["host_disagrees"], True)

    def test_the_run_carries_cost_and_errors(self):
        import score

        results = [self.score(200, {**READ, "seconds": 3.0}), self.score(403, {"error": "Forbidden"})]
        report = score.summarize("hosted", results, 8.0)
        score.add_hosted_facts(report, results, self.HOST[0])
        self.assertEqual(report["pages"], 1)
        self.assertEqual(report["http_errors"], ["01-northline 403"])
        self.assertAlmostEqual(report["cost_per_invoice_usd"], 0.005, places=3)
        self.assertEqual(report["url_region"], "us-east-1")


if __name__ == "__main__":
    unittest.main()
