"""A date or amount that is printed but does not parse raises an exception.

The rule is DESIGN.md's: the reason is unparsed_<field>, kept apart from
missing_<field>, because the field is on the page and needs a person to read
it. Before this rule an unparsed field went straight through as a silent
error. An unparsed invoice date also stops terms from setting a due date, so
that invoice carries both unparsed_invoice_date and missing_due_date.

No network call. Textract gets a stand-in client, Docling a stand-in reply.
Nothing here reads a holdout.

Run from the checkout:
  python sandbox/test_unparsed.py
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

import page  # noqa: E402
import read_invoice  # noqa: E402
from read_invoice import exception_reasons, parsed, read_invoice as read  # noqa: E402
from test_runners import Base, FakeTextract, docling_reply, expense_field, expense_response  # noqa: E402

CLEAN = {
    "supplier": "Harbor Paper Co.",
    "invoice_number": "HP-2207",
    "invoice_date": "03/14/2026",
    "due_date": "04/13/2026",
    "subtotal": "500.00",
    "tax": "40.00",
    "total": "540.00",
}


def reasons(**raw) -> list[str]:
    return exception_reasons(parsed({**CLEAN, **raw}))


class Rule(unittest.TestCase):
    def test_a_clean_read_raises_nothing(self):
        self.assertEqual(reasons(), [])

    def test_each_date_that_does_not_parse(self):
        self.assertEqual(reasons(invoice_date="the fourteenth"), ["unparsed_invoice_date"])
        self.assertEqual(reasons(due_date="TBD"), ["unparsed_due_date"])

    def test_each_amount_that_does_not_parse(self):
        self.assertEqual(reasons(subtotal="five hundred"), ["unparsed_subtotal"])
        self.assertEqual(reasons(tax="pending"), ["unparsed_tax"])
        self.assertEqual(reasons(total="see attached"), ["unparsed_total"])

    def test_unparsed_is_not_missing(self):
        self.assertNotIn("missing_tax", reasons(tax="pending"))
        self.assertEqual(reasons(tax=None), ["missing_tax"])

    def test_unparsed_invoice_date_leaves_terms_no_date(self):
        got = reasons(invoice_date="the fourteenth", due_date=None, terms="Net 30")
        self.assertEqual(got, ["missing_due_date", "unparsed_invoice_date"])

    def test_an_unparsed_amount_does_not_also_claim_a_mismatch(self):
        self.assertEqual(reasons(total="see attached"), ["unparsed_total"])

    def test_text_fields_are_never_unparsed(self):
        got = reasons(supplier="???", invoice_number="#")
        self.assertFalse([r for r in got if r.startswith("unparsed_")])


class Readers(Base):
    def test_textract_unparsed_date_goes_to_a_person(self):
        response = expense_response([
            expense_field("VENDOR_NAME", "Harbor Paper Co."),
            expense_field("INVOICE_RECEIPT_ID", "HP-2207"),
            expense_field("INVOICE_RECEIPT_DATE", "the fourteenth"),
            expense_field("DUE_DATE", "04/13/2026"),
            expense_field("SUBTOTAL", "$500.00"),
            expense_field("TAX", "$40.00"),
            expense_field("TOTAL", "$540.00"),
        ])
        out = read(self.pdf, client=FakeTextract(response))
        self.assertEqual(out["invoice_date"], "unparsed")
        self.assertIs(out["exception"], True)
        self.assertEqual(out["exception_reasons"], ["unparsed_invoice_date"])

    def test_docling_unparsed_date_goes_to_a_person(self):
        doc = {"texts": [
            {"text": "Harbor Paper Co.", "label": "section_header"},
            {"text": "Invoice Number: HP-2207", "label": "text"},
            {"text": "Invoice Date: the fourteenth", "label": "text"},
            {"text": "Due Date: 04/13/2026", "label": "text"},
            {"text": "Subtotal $500.00", "label": "text"},
            {"text": "Tax $40.00", "label": "text"},
            {"text": "Total $540.00", "label": "text"},
        ], "tables": []}
        with mock.patch.object(read_invoice.urllib.request, "urlopen", return_value=docling_reply(doc)):
            out = read(self.pdf, "docling")
        self.assertEqual(out["invoice_date"], "unparsed")
        self.assertEqual(out["exception_reasons"], ["unparsed_invoice_date"])


class Page(unittest.TestCase):
    def test_every_unparsed_reason_has_words(self):
        for name in read_invoice.DATE_FIELDS + read_invoice.MONEY_FIELDS:
            text = page.reason_text(f"unparsed_{name}")
            self.assertIn("could not be read", text, name)


if __name__ == "__main__":
    unittest.main()
