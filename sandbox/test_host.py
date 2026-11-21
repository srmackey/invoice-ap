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
for path in (CHECKOUT, CHECKOUT / "host"):
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


if __name__ == "__main__":
    unittest.main()
