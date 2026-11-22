"""Field labels in other languages, for the fields Textract misses.

The rule is DESIGN.md's. Textract types English labels well and foreign ones
badly: it leaves "Rechnungsdatum" or "Factuurnummer" untyped, and types
"Fällig am" as the invoice date. So a known due-date label overrides the type
Textract gave; an untyped field under a known invoice-number or invoice-date
label takes that field; and a field still missing takes the value printed
beside its label on the page. Docling's mapper takes the foreign due-date
labels too.

No network call. Nothing here reads a holdout.

Run from the checkout:
  python sandbox/test_labels.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parent
for path in (CHECKOUT, HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import read_invoice  # noqa: E402
from read_invoice import read_invoice as read, textract_fields, value_beside_label, DUE_LABELS  # noqa: E402
from test_runners import Base, FakeTextract, docling_reply, expense_field, expense_response  # noqa: E402


def labeled(kind: str, label: str, text: str) -> dict:
    field = expense_field(kind, text)
    field["LabelDetection"] = {"Text": label, "Confidence": 99.0}
    return field


def line(text: str, left: float, top: float) -> dict:
    return {"BlockType": "LINE", "Text": text, "Geometry": {"BoundingBox": {"Left": left, "Top": top}}}


class Textract(unittest.TestCase):
    def fields(self, summary: list[dict], lines: list[dict] | None = None) -> dict:
        response = expense_response(summary)
        if lines is not None:
            response["ExpenseDocuments"][0]["Blocks"] = lines
        return textract_fields(response)

    def test_a_due_label_typed_as_the_invoice_date_is_the_due_date(self):
        got = self.fields([
            labeled("INVOICE_RECEIPT_DATE", "Rechnungsdatum", "18. August 2026"),
            labeled("INVOICE_RECEIPT_DATE", "Fällig am", "17. September 2026"),
        ])
        self.assertEqual(got["invoice_date"], "18. August 2026")
        self.assertEqual(got["due_date"], "17. September 2026")

    def test_untyped_fields_under_known_labels(self):
        got = self.fields([
            labeled("OTHER", "Rechnungsdatum", "20. März 2026"),
            labeled("OTHER", "Factuurnummer", "VLK-2081"),
            labeled("OTHER", "Échéance", "07/11/2026"),
        ])
        self.assertEqual(got["invoice_date"], "20. März 2026")
        self.assertEqual(got["invoice_number"], "VLK-2081")
        self.assertEqual(got["due_date"], "07/11/2026")

    def test_an_untyped_field_under_another_label_is_ignored(self):
        got = self.fields([labeled("OTHER", "Bank", "IBAN DE00 0000")])
        self.assertIsNone(got["invoice_number"])

    def test_a_typed_english_field_keeps_its_type(self):
        got = self.fields([labeled("INVOICE_RECEIPT_DATE", "Date", "03/14/2026")])
        self.assertEqual(got["invoice_date"], "03/14/2026")
        self.assertIsNone(got["due_date"])

    def test_the_value_beside_a_label_on_the_page(self):
        got = self.fields([], [
            line("N.° de factura", 0.28, 0.151), line("HA-7932", 0.47, 0.151),
            line("Vencimiento", 0.28, 0.194), line("15/04/2026", 0.47, 0.194),
        ])
        self.assertEqual(got["invoice_number"], "HA-7932")
        self.assertEqual(got["due_date"], "15/04/2026")

    def test_the_value_after_a_label_on_the_same_line(self):
        self.assertEqual(value_beside_label([("Scadenza: 21/10/2026", {})], DUE_LABELS), "21/10/2026")

    def test_a_value_on_another_row_is_not_taken(self):
        self.assertIsNone(value_beside_label(
            [("Fällig am", {"Left": 0.28, "Top": 0.19}), ("17.09.2026", {"Left": 0.47, "Top": 0.30})], DUE_LABELS))

    def test_an_empty_typed_value_does_not_block_the_page(self):
        got = self.fields([labeled("INVOICE_RECEIPT_ID", "Rechnungsnr.", "")],
                          [line("Rechnungsnr.", 0.28, 0.15), line("KEK-3518", 0.47, 0.15)])
        self.assertEqual(got["invoice_number"], "KEK-3518")


class Docling(Base):
    def test_a_foreign_due_label(self):
        doc = {"texts": [
            {"text": "Weserland Antriebstechnik GmbH", "label": "section_header"},
            {"text": "Invoice Number: WAT-3106", "label": "text"},
            {"text": "Invoice Date: 08/18/2026", "label": "text"},
            {"text": "Fällig am: 09/17/2026", "label": "text"},
            {"text": "Subtotal $500.00", "label": "text"},
            {"text": "Tax $40.00", "label": "text"},
            {"text": "Total $540.00", "label": "text"},
        ], "tables": []}
        with mock.patch.object(read_invoice.urllib.request, "urlopen", return_value=docling_reply(doc)):
            out = read(self.pdf, "docling")
        self.assertEqual(out["due_date"], "2026-09-17")


if __name__ == "__main__":
    unittest.main()
