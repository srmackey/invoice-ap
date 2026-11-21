"""Due dates from printed terms, and the amount due standing in for a missing total.

The rule is DESIGN.md's: "Net N" and "2/10 Net 30" are due N days after the
invoice date, "Due on receipt" on the invoice date, and end-of-month terms
leave the due date for a person. A printed due date always wins. When no
total is printed, the total is the printed amount due.

No network call. Textract gets a stand-in client, Docling a stand-in reply,
both from test_runners.py. Nothing here reads the holdout.

Run from the checkout:
  python sandbox/test_terms.py
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parent
for path in (CHECKOUT, HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import read_invoice  # noqa: E402
from read_invoice import UNCOMPUTABLE, parsed, read_invoice as read, terms_days  # noqa: E402
from test_runners import Base, FakeTextract, docling_reply, expense_field, expense_response  # noqa: E402

HEADER = [
    expense_field("VENDOR_NAME", "Harbor Paper Co."),
    expense_field("INVOICE_RECEIPT_ID", "HP-2207"),
    expense_field("INVOICE_RECEIPT_DATE", "03/14/2026"),
    expense_field("SUBTOTAL", "$500.00"),
    expense_field("TAX", "$40.00"),
]


def textract_with(*fields: dict) -> FakeTextract:
    return FakeTextract(expense_response(HEADER + list(fields)))


def other_field(label: str, text: str) -> dict:
    field = expense_field("OTHER", text)
    field["LabelDetection"] = {"Text": label, "Confidence": 99.0}
    return field


def docling_doc(*lines: str, tables: list | None = None) -> dict:
    texts = [
        {"text": "Harbor Paper Co.", "label": "section_header"},
        {"text": "Invoice Number: HP-2207", "label": "text"},
        {"text": "Invoice Date: 03/14/2026", "label": "text"},
    ]
    texts += [{"text": line, "label": "text"} for line in lines]
    texts += [
        {"text": "Subtotal $500.00", "label": "text"},
        {"text": "Tax $40.00", "label": "text"},
    ]
    return {"texts": texts, "tables": tables or []}


def table(rows: list[list[str]]) -> dict:
    cells = []
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            cells.append({"text": text, "start_row_offset_idx": r, "start_col_offset_idx": c})
    return {"data": {"table_cells": cells}}


class TermsForms(unittest.TestCase):
    def test_net_n(self):
        for text, days in [("Net 30", 30), ("NET 45 DAYS", 45), ("net15", 15), ("Terms: Net 10", 10)]:
            self.assertEqual(terms_days(text), days, text)

    def test_discount_terms_use_the_net_days(self):
        for text in ("2/10 Net 30", "2% 10, Net 30", "2/10 n/30", "1/15 Net 30 days"):
            self.assertEqual(terms_days(text), 30, text)

    def test_due_on_receipt(self):
        for text in ("Due on receipt", "DUE UPON RECEIPT", "Payable on receipt", "Upon Receipt"):
            self.assertEqual(terms_days(text), 0, text)

    def test_end_of_month_is_for_a_person(self):
        for text in ("Net 30 EOM", "2/10 EOM", "Net 30 end of month", "10th prox", "Net 30 E.O.M."):
            self.assertEqual(terms_days(text), UNCOMPUTABLE, text)

    def test_anything_else_is_not_terms(self):
        for text in ("Payment due in 30 days", "COD", "Thank you", "Net amount 30.00", "Net 1,200.00", "", None):
            self.assertIsNone(terms_days(text), text)


class ParsedRule(unittest.TestCase):
    BASE = {"invoice_date": "03/14/2026", "subtotal": "500.00", "tax": "40.00", "total": "540.00"}

    def test_terms_set_a_missing_due_date(self):
        got = parsed({**self.BASE, "terms": "Net 30"})
        self.assertEqual(got["due_date"], date(2026, 4, 13))

    def test_printed_due_date_wins(self):
        got = parsed({**self.BASE, "due_date": "03/20/2026", "terms": "Net 30"})
        self.assertEqual(got["due_date"], date(2026, 3, 20))

    def test_unparsed_printed_date_is_not_replaced_by_terms(self):
        got = parsed({**self.BASE, "due_date": "the 20th", "terms": "Net 30"})
        self.assertEqual(got["due_date"], "unparsed")

    def test_terms_printed_as_the_due_date(self):
        got = parsed({**self.BASE, "due_date": "Upon receipt"})
        self.assertEqual(got["due_date"], date(2026, 3, 14))

    def test_eom_leaves_it_missing(self):
        self.assertIsNone(parsed({**self.BASE, "terms": "Net 30 EOM"})["due_date"])
        self.assertIsNone(parsed({**self.BASE, "due_date": "Net 30 EOM"})["due_date"])

    def test_no_invoice_date_no_due_date(self):
        got = parsed({**self.BASE, "invoice_date": None, "terms": "Net 30"})
        self.assertIsNone(got["due_date"])

    def test_amount_due_stands_in_for_a_missing_total(self):
        got = parsed({**self.BASE, "total": None, "amount_due": "$540.00"})
        self.assertEqual(str(got["total"]), "540.00")

    def test_printed_total_wins_over_amount_due(self):
        got = parsed({**self.BASE, "amount_due": "$40.00"})
        self.assertEqual(str(got["total"]), "540.00")


class TextractTerms(Base):
    def test_payment_terms_type(self):
        out = read(self.pdf, client=textract_with(expense_field("PAYMENT_TERMS", "Net 30"), expense_field("TOTAL", "$540.00")))
        self.assertEqual(out["due_date"], "2026-04-13")
        self.assertIs(out["exception"], False)

    def test_discount_terms(self):
        out = read(self.pdf, client=textract_with(expense_field("PAYMENT_TERMS", "2/10 Net 30"), expense_field("TOTAL", "$540.00")))
        self.assertEqual(out["due_date"], "2026-04-13")

    def test_due_on_receipt_in_the_due_date_slot(self):
        out = read(self.pdf, client=textract_with(expense_field("DUE_DATE", "Due on receipt"), expense_field("TOTAL", "$540.00")))
        self.assertEqual(out["due_date"], "2026-03-14")

    def test_untyped_terms_under_a_terms_label(self):
        out = read(self.pdf, client=textract_with(other_field("Terms:", "Net 15"), expense_field("TOTAL", "$540.00")))
        self.assertEqual(out["due_date"], "2026-03-29")

    def test_untyped_field_without_a_terms_label_is_ignored(self):
        out = read(self.pdf, client=textract_with(other_field("Ship via", "Net 15"), expense_field("TOTAL", "$540.00")))
        self.assertIsNone(out["due_date"])

    def test_eom_raises_missing_due_date(self):
        out = read(self.pdf, client=textract_with(expense_field("PAYMENT_TERMS", "Net 30 EOM"), expense_field("TOTAL", "$540.00")))
        self.assertIsNone(out["due_date"])
        self.assertEqual(out["exception_reasons"], ["missing_due_date"])

    def test_amount_due_when_no_total(self):
        out = read(self.pdf, client=textract_with(expense_field("DUE_DATE", "04/13/2026"), expense_field("AMOUNT_DUE", "$540.00")))
        self.assertEqual(out["total"], "540.00")
        self.assertIs(out["exception"], False)

    def test_amount_due_after_a_deposit_is_an_exception(self):
        out = read(self.pdf, client=textract_with(expense_field("DUE_DATE", "04/13/2026"), expense_field("AMOUNT_DUE", "$340.00")))
        self.assertEqual(out["total"], "340.00")
        self.assertEqual(out["exception_reasons"], ["total_mismatch"])

    def test_total_wins_when_both_print(self):
        out = read(
            self.pdf,
            client=textract_with(
                expense_field("DUE_DATE", "04/13/2026"),
                expense_field("TOTAL", "$540.00"),
                expense_field("AMOUNT_DUE", "$340.00"),
            ),
        )
        self.assertEqual(out["total"], "540.00")


class DoclingTerms(Base):
    def read_docling(self, doc: dict) -> dict:
        with mock.patch.object(read_invoice.urllib.request, "urlopen", return_value=docling_reply(doc)):
            return read(self.pdf, "docling")

    def test_terms_after_a_label_on_the_same_line(self):
        out = self.read_docling(docling_doc("Terms: Net 30", "Total $540.00"))
        self.assertEqual(out["due_date"], "2026-04-13")
        self.assertIs(out["exception"], False)

    def test_terms_on_the_line_after_the_label(self):
        out = self.read_docling(docling_doc("Payment Terms", "2/10 Net 30", "Total $540.00"))
        self.assertEqual(out["due_date"], "2026-04-13")

    def test_terms_line_on_its_own(self):
        out = self.read_docling(docling_doc("Due on receipt", "Total $540.00"))
        self.assertEqual(out["due_date"], "2026-03-14")

    def test_terms_in_a_table(self):
        out = self.read_docling(docling_doc("Total $540.00", tables=[table([["Terms", "Net 45"]])]))
        self.assertEqual(out["due_date"], "2026-04-28")

    def test_due_date_label_holding_terms(self):
        out = self.read_docling(docling_doc("Due Date: Upon receipt", "Total $540.00"))
        self.assertEqual(out["due_date"], "2026-03-14")

    def test_eom_raises_missing_due_date(self):
        out = self.read_docling(docling_doc("Terms: Net 30 EOM", "Total $540.00"))
        self.assertIsNone(out["due_date"])
        self.assertEqual(out["exception_reasons"], ["missing_due_date"])

    def test_printed_due_date_wins(self):
        out = self.read_docling(docling_doc("Due Date: 03/20/2026", "Terms: Net 30", "Total $540.00"))
        self.assertEqual(out["due_date"], "2026-03-20")

    def test_amount_due_when_no_total(self):
        for line in ("Amount Due $540.00", "Balance Due: $540.00", "Total Due $540.00"):
            out = self.read_docling(docling_doc("Terms: Net 30", line))
            self.assertEqual(out["total"], "540.00", line)
            self.assertIs(out["exception"], False, line)

    def test_amount_due_on_the_next_line(self):
        out = self.read_docling(docling_doc("Terms: Net 30", "Amount Due", "$540.00"))
        self.assertEqual(out["total"], "540.00")


class SameRuleEitherRunner(Base):
    def test_both_runners_compute_the_same_due_date(self):
        textract = read(self.pdf, client=textract_with(expense_field("PAYMENT_TERMS", "Net 30"), expense_field("AMOUNT_DUE", "$540.00")))
        with mock.patch.object(read_invoice.urllib.request, "urlopen", return_value=docling_reply(docling_doc("Terms: Net 30", "Amount Due $540.00"))):
            docling = read(self.pdf, "docling")
        for name in read_invoice.FIELDS:
            self.assertEqual(textract[name], docling[name], name)


if __name__ == "__main__":
    unittest.main()
