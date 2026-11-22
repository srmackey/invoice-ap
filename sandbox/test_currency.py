"""Page origin: the invoice's currency, decimal-comma amounts, and currency as date evidence.

The rule is DESIGN.md's. A currency other than the US dollar on the amounts
raises foreign_currency, and the result carries the code; nothing is
converted. The currency is Textract's code on the amounts, else an untyped
"Currency" field, else a sign or code printed on an amount. An amount shows its
own decimal mark when it can; a EUR page settles one that cannot ("1.234").
A non-US currency, or an amount written with a decimal comma, reads an
ambiguous date day-first, below a date on the page that only one order can
read.

No network call. Nothing here reads a holdout.

Run from the checkout:
  python sandbox/test_currency.py
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parent
for path in (CHECKOUT, HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from read_invoice import as_money, currency_in, exception_reasons, page_currency, parsed, textract_fields  # noqa: E402
from test_runners import expense_field, expense_response  # noqa: E402

BASE = {"supplier": "X", "invoice_number": "1", "invoice_date": "14/03/2026", "due_date": "13/04/2026",
        "subtotal": "500.00", "tax": "40.00", "total": "540.00"}


class Amounts(unittest.TestCase):
    def test_the_amount_shows_its_decimal_mark(self):
        cases = {"$1,234.56": "1234.56", "1.234,56 €": "1234.56", "3872,40€": "3872.40", "1 234,56": "1234.56",
                 "1 234,56 €": "1234.56", "£980.00": "980.00", "1,234": "1234.00", "1.234.567": "1234567.00",
                 "5,819.57": "5819.57", "USD 1,234.56": "1234.56", "0,00 €": "0.00", "12.5": "12.50"}
        for text, want in cases.items():
            self.assertEqual(as_money(text), Decimal(want), text)

    def test_only_a_lone_dot_before_three_digits_needs_the_page(self):
        self.assertEqual(as_money("1.234"), Decimal("1.23"))
        self.assertEqual(as_money("1.234", comma_decimal=True), Decimal("1234.00"))
        self.assertEqual(as_money("1.234,50", comma_decimal=False), Decimal("1234.50"))

    def test_what_is_not_an_amount(self):
        self.assertEqual(as_money("CR 12.00"), "unparsed")
        self.assertEqual(as_money("see attached"), "unparsed")
        self.assertIsNone(as_money("N/A"))


class Currency(unittest.TestCase):
    def test_signs_and_codes(self):
        cases = {"1.234,56 €": "EUR", "EUR 10.00": "EUR", "£980.00": "GBP", "$5.00 CAD": "CAD", "C$5.00": "CAD",
                 "USD 5.00": "USD", "US$5.00": "USD", "$5.00": None, "5.00": None}
        for text, want in cases.items():
            self.assertEqual(currency_in(text), want, text)

    def test_the_runner_first_then_the_amounts(self):
        self.assertEqual(page_currency({**BASE, "currency": "GBP", "total": "€540.00"}), "GBP")
        self.assertEqual(page_currency({**BASE, "total": "540,00 €"}), "EUR")
        self.assertIsNone(page_currency(BASE))

    def test_textract_code_on_the_amounts(self):
        total = expense_field("TOTAL", "540,00 €")
        total["Currency"] = {"Code": "EUR", "Confidence": 98.0}
        self.assertEqual(textract_fields(expense_response([total]))["currency"], "EUR")

    def test_textract_currency_field(self):
        field = expense_field("OTHER", "GBP")
        field["LabelDetection"] = {"Text": "Currency:", "Confidence": 99.0}
        got = textract_fields(expense_response([field, expense_field("TOTAL", "980.00")]))
        self.assertEqual(got["currency"], "GBP")


class Exception_(unittest.TestCase):
    def test_foreign_currency_goes_to_a_person(self):
        values = parsed({**BASE, "currency": "EUR"})
        self.assertEqual(values["currency"], "EUR")
        self.assertEqual(exception_reasons(values), ["foreign_currency"])

    def test_us_dollars_and_no_currency_do_not(self):
        self.assertEqual(exception_reasons(parsed({**BASE, "currency": "USD"})), [])
        self.assertEqual(exception_reasons(parsed(BASE)), [])

    def test_euro_amounts_read_and_add_up(self):
        values = parsed({**BASE, "currency": "EUR", "subtotal": "1.000,00 €", "tax": "190,00 €", "total": "1.190,00 €"})
        self.assertEqual(values["total"], Decimal("1190.00"))
        self.assertEqual(exception_reasons(values), ["foreign_currency"])


class DateEvidence(unittest.TestCase):
    AMBIGUOUS = {**BASE, "invoice_date": "03/04/2026", "due_date": "05/05/2026"}

    def test_a_foreign_currency_reads_an_ambiguous_date_day_first(self):
        self.assertEqual(parsed({**self.AMBIGUOUS, "currency": "EUR"})["invoice_date"], date(2026, 4, 3))

    def test_us_dollars_leave_it_month_first(self):
        self.assertEqual(parsed({**self.AMBIGUOUS, "currency": "USD"})["invoice_date"], date(2026, 3, 4))
        self.assertEqual(parsed(self.AMBIGUOUS)["invoice_date"], date(2026, 3, 4))

    def test_a_decimal_comma_reads_it_day_first_even_in_dollars(self):
        got = parsed({**self.AMBIGUOUS, "currency": "USD", "subtotal": "7.996,92", "tax": "0,00", "total": "7.996,92"})
        self.assertEqual(got["invoice_date"], date(2026, 4, 3))
        got = parsed({**self.AMBIGUOUS, "subtotal": "5 784,33", "tax": "0.00", "total": "5 784,33"})
        self.assertEqual(got["invoice_date"], date(2026, 4, 3))

    def test_us_amounts_are_not_evidence(self):
        got = parsed({**self.AMBIGUOUS, "subtotal": "1,234.00", "tax": "0.00", "total": "1,234.00"})
        self.assertEqual(got["invoice_date"], date(2026, 3, 4))

    def test_a_month_first_date_on_the_page_beats_the_currency(self):
        got = parsed({**BASE, "currency": "EUR", "invoice_date": "03/04/2026", "due_date": "04/15/2026"})
        self.assertEqual(got["invoice_date"], date(2026, 3, 4))


class Scorer(unittest.TestCase):
    def test_a_hosted_run_keeps_the_hosts_currency(self):
        import score

        body = {"supplier": "X", "invoice_number": "1", "invoice_date": "2026-03-14", "due_date": "2026-04-13",
                "subtotal": "500.00", "tax": "40.00", "total": "540.00", "currency": "EUR", "exception": True,
                "exception_reasons": ["foreign_currency"], "printed": {}, "error": None, "runner": "t", "pages": 1}
        row = {"id": "x", "file": "unused.pdf", **{k: body[k] for k in ("supplier", "invoice_number", "invoice_date",
               "due_date", "subtotal", "tax", "total")}, "exception": True}
        with mock.patch.object(score.host_call, "call_reader", return_value=(200, body)):
            item = score.score_invoice("hosted", row, Path("unused"), host=("https://a.lambda-url.us-east-1.on.aws/", object()))
        self.assertEqual(item["currency"], "EUR")
        self.assertNotIn("host_disagrees", item)


if __name__ == "__main__":
    unittest.main()
