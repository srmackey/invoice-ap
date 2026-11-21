"""Both runners go through read_invoice.py's mapping and the exception rule.

No network call. Textract gets a stand-in client that returns the shape
AnalyzeExpense returns. Docling gets a stand-in for the HTTP response.

Run from the checkout:
  python sandbox/test_runners.py
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

CHECKOUT = Path(__file__).resolve().parent.parent
if str(CHECKOUT) not in sys.path:
    sys.path.insert(0, str(CHECKOUT))

import read_invoice  # noqa: E402
from read_invoice import FIELDS, read_fields, read_invoice as read, textract_fields  # noqa: E402

NORTHLINE = {
    "supplier": "Northline Supply Co.",
    "invoice_number": "NL-10442",
    "invoice_date": "2026-02-03",
    "due_date": "2026-03-05",
    "subtotal": "1260.00",
    "tax": "100.80",
    "total": "1360.80",
}


def expense_field(kind: str, text: str, confidence: float = 99.0) -> dict:
    return {
        "Type": {"Text": kind, "Confidence": 99.0},
        "ValueDetection": {"Text": text, "Confidence": confidence},
    }


def expense_response(fields: list[dict], pages: int = 1) -> dict:
    return {"DocumentMetadata": {"Pages": pages}, "ExpenseDocuments": [{"SummaryFields": fields}]}


NORTHLINE_EXPENSE = expense_response(
    [
        expense_field("VENDOR_NAME", "Northline Supply Co."),
        expense_field("INVOICE_RECEIPT_ID", "NL-10442"),
        expense_field("INVOICE_RECEIPT_DATE", "February 3, 2026"),
        expense_field("DUE_DATE", "March 5, 2026"),
        expense_field("SUBTOTAL", "$1,260.00"),
        expense_field("TAX", "$100.80"),
        expense_field("TOTAL", "$1,360.80"),
        expense_field("ADDRESS", "12 Mill Road"),
    ]
)

NORTHLINE_DOCLING = {
    "texts": [
        {"text": "Northline Supply Co.", "label": "section_header"},
        {"text": "Invoice Number: NL-10442", "label": "text"},
        {"text": "Invoice Date: February 3, 2026", "label": "text"},
        {"text": "Due Date: March 5, 2026", "label": "text"},
        {"text": "Subtotal $1,260.00", "label": "text"},
        {"text": "Tax $100.80", "label": "text"},
        {"text": "Total $1,360.80", "label": "text"},
    ],
    "tables": [],
}


class FakeTextract:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def analyze_expense(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class FakeHTTP(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def docling_reply(doc: dict) -> FakeHTTP:
    payload = {"status": "success", "document": {"json_content": doc}}
    return FakeHTTP(json.dumps(payload).encode("utf-8"))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pdf = Path(self.tmp.name) / "01-northline.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 stand-in")

    def tearDown(self):
        self.tmp.cleanup()


class TextractMapping(unittest.TestCase):
    def test_seven_fields(self):
        got = textract_fields(NORTHLINE_EXPENSE)
        self.assertEqual(set(got), set(FIELDS) | {"amount_due", "terms"})
        self.assertEqual(got["supplier"], "Northline Supply Co.")
        self.assertEqual(got["total"], "$1,360.80")

    def test_missing_field_is_none(self):
        got = textract_fields(expense_response([expense_field("TOTAL", "10.00")]))
        self.assertIsNone(got["due_date"])
        self.assertEqual(got["total"], "10.00")

    def test_highest_confidence_wins(self):
        got = textract_fields(
            expense_response(
                [
                    expense_field("VENDOR_NAME", "Bill To Corp", 40.0),
                    expense_field("VENDOR_NAME", "Northline Supply Co.", 95.0),
                ]
            )
        )
        self.assertEqual(got["supplier"], "Northline Supply Co.")

    def test_empty_response(self):
        self.assertTrue(all(value is None for value in textract_fields({}).values()))


class TextractRunner(Base):
    def test_default_runner_is_textract(self):
        client = FakeTextract(NORTHLINE_EXPENSE)
        out = read(self.pdf, client=client)
        self.assertEqual(out["runner"], "textract-analyze-expense")
        self.assertEqual(client.calls, [{"Document": {"Bytes": b"%PDF-1.4 stand-in"}}])
        for name, want in NORTHLINE.items():
            self.assertEqual(out[name], want, name)
        self.assertIs(out["exception"], False)
        self.assertIsNone(out["error"])

    def test_pages_come_back_for_cost(self):
        _raw, _used, _error, pages = read_fields(self.pdf, "textract", FakeTextract(expense_response([], pages=2)))
        self.assertEqual(pages, 2)

    def test_exception_rule_applies(self):
        fields = [f for f in NORTHLINE_EXPENSE["ExpenseDocuments"][0]["SummaryFields"] if f["Type"]["Text"] != "DUE_DATE"]
        out = read(self.pdf, client=FakeTextract(expense_response(fields)))
        self.assertIs(out["exception"], True)
        self.assertEqual(out["exception_reasons"], ["missing_due_date"])

    def test_a_failed_call_is_the_error(self):
        out = read(self.pdf, client=FakeTextract(error=RuntimeError("expired token")))
        self.assertEqual(out["error"], "textract request failed: expired token")
        self.assertTrue(all(out[name] is None for name in FIELDS))
        self.assertIs(out["exception"], True)


class DoclingRunner(Base):
    def test_docling_is_the_backup(self):
        with mock.patch.object(read_invoice.urllib.request, "urlopen", return_value=docling_reply(NORTHLINE_DOCLING)):
            out = read(self.pdf, "docling")
        self.assertEqual(out["runner"], "docling")
        for name, want in NORTHLINE.items():
            self.assertEqual(out[name], want, name)
        self.assertIs(out["exception"], False)

    def test_docling_down_is_the_error(self):
        refused = read_invoice.urllib.error.URLError("refused")
        with mock.patch.object(read_invoice.urllib.request, "urlopen", side_effect=refused):
            out = read(self.pdf, "docling")
        self.assertEqual(out["error"], "docling request failed: refused")


class SameReadEitherWay(Base):
    def test_both_runners_agree_on_the_same_invoice(self):
        textract = read(self.pdf, client=FakeTextract(NORTHLINE_EXPENSE))
        with mock.patch.object(read_invoice.urllib.request, "urlopen", return_value=docling_reply(NORTHLINE_DOCLING)):
            docling = read(self.pdf, "docling")
        for name in FIELDS:
            self.assertEqual(textract[name], docling[name], name)

    def test_unknown_runner_is_refused(self):
        with self.assertRaises(ValueError):
            read(self.pdf, "tesseract")

    def test_record_names_the_runner(self):
        out = read(self.pdf, client=FakeTextract(NORTHLINE_EXPENSE))
        record = read_invoice.make_record(self.pdf, out, 1.23)
        self.assertEqual(record["runner"], "textract-analyze-expense")
        self.assertEqual(record["seconds"], 1.2)


if __name__ == "__main__":
    unittest.main()
