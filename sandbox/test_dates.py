"""How a printed date is read.

The rule is DESIGN.md's. Dates read through dateutil, strictly first and then
fuzzily for a date inside a sentence. Month words in French, German, Dutch,
Italian, and Spanish read like English ones. A date counts only when the text
gives a day, a month, and a year, and the year is 1900 through next year;
anything else printed is unparsed. A numeric date that only one order can read
is read that way; a dotted one is read day-first; an ambiguous one is read
month-first unless the other date on the page can only be day-first.

The cases here are generic, written for this file. Nothing here reads a
holdout.

Run from the checkout:
  python sandbox/test_dates.py
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parent
for path in (CHECKOUT, HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from read_invoice import as_date, exception_reasons, numeric_order, parsed  # noqa: E402

D = date


class Orders(unittest.TestCase):
    def test_numeric_orders(self):
        cases = {"2026-03-04": "year", "14/03/2026": "day", "03/14/2026": "month", "04.03.2026": "dotted",
                 "03/04/2026": "either", "3-4-26": "either", "March 4, 2026": None, "": None, None: None}
        for text, want in cases.items():
            self.assertEqual(numeric_order(text), want, text)


class Reading(unittest.TestCase):
    def test_what_only_one_order_reads(self):
        self.assertEqual(as_date("03/14/2026"), D(2026, 3, 14))
        self.assertEqual(as_date("14/03/2026"), D(2026, 3, 14))
        self.assertEqual(as_date("14.03.26"), D(2026, 3, 14))
        self.assertEqual(as_date("2026-03-04"), D(2026, 3, 4))
        self.assertEqual(as_date("2026/03/04"), D(2026, 3, 4))

    def test_ambiguous_is_month_first_unless_told(self):
        self.assertEqual(as_date("03/04/2026"), D(2026, 3, 4))
        self.assertEqual(as_date("03/04/2026", day_first=True), D(2026, 4, 3))
        self.assertEqual(as_date("3/4/26"), D(2026, 3, 4))

    def test_dotted_is_day_first(self):
        self.assertEqual(as_date("04.03.2026"), D(2026, 3, 4))
        self.assertEqual(as_date("4.3.2026"), D(2026, 3, 4))

    def test_english_words(self):
        for text in ("March 4, 2026", "Mar 4 2026", "4 March 2026", "04-Mar-2026", "Wednesday, March 4, 2026"):
            self.assertEqual(as_date(text), D(2026, 3, 4), text)

    def test_other_languages(self):
        cases = {
            "4. März 2026": D(2026, 3, 4), "17 märz 2026": D(2026, 3, 17), "4 mars 2026": D(2026, 3, 4),
            "1er août 2026": D(2026, 8, 1), "4 févr. 2026": D(2026, 2, 4), "12 mrt 2026": D(2026, 3, 12),
            "4 maart 2026": D(2026, 3, 4), "4 marzo 2026": D(2026, 3, 4), "4 de marzo de 2026": D(2026, 3, 4),
            "30 dicembre 2026": D(2026, 12, 30), "2 okt. 2026": D(2026, 10, 2),
        }
        for text, want in cases.items():
            self.assertEqual(as_date(text), want, text)

    def test_a_date_inside_a_sentence(self):
        self.assertEqual(as_date("Payment is due by March 4, 2026"), D(2026, 3, 4))

    def test_two_digit_years(self):
        self.assertEqual(as_date("3/4/26"), D(2026, 3, 4))
        self.assertEqual(as_date("3/4/86"), D(1986, 3, 4))


class Guard(unittest.TestCase):
    """Text missing a part, or outside the years, is unparsed, never filled in."""

    def test_missing_a_part(self):
        for text in ("March 2026", "12/2026", "the 4th", "Net 30", "30 days from delivery"):
            self.assertEqual(as_date(text), "unparsed", text)

    def test_amounts_and_words(self):
        for text in ("1360.80", "TBC", "Upon receipt", "à convenir", "see delivery note"):
            self.assertEqual(as_date(text), "unparsed", text)

    def test_impossible_and_out_of_range(self):
        for text in ("2/30/2026", "1/2/1360", f"1/2/{date.today().year + 2}"):
            self.assertEqual(as_date(text), "unparsed", text)

    def test_nothing_printed(self):
        self.assertIsNone(as_date(None))
        self.assertIsNone(as_date(""))


class PageEvidence(unittest.TestCase):
    BASE = {"subtotal": "500.00", "tax": "40.00", "total": "540.00"}

    def test_a_day_first_due_date_turns_the_invoice_date(self):
        got = parsed({**self.BASE, "invoice_date": "03/04/2026", "due_date": "17/04/2026"})
        self.assertEqual(got["invoice_date"], D(2026, 4, 3))

    def test_a_day_first_invoice_date_turns_the_due_date(self):
        got = parsed({**self.BASE, "invoice_date": "17/03/2026", "due_date": "02/04/2026"})
        self.assertEqual(got["due_date"], D(2026, 4, 2))

    def test_no_evidence_stays_month_first(self):
        got = parsed({**self.BASE, "invoice_date": "03/04/2026", "due_date": "04/05/2026"})
        self.assertEqual(got["invoice_date"], D(2026, 3, 4))
        self.assertEqual(got["due_date"], D(2026, 4, 5))

    def test_a_dotted_date_is_not_evidence(self):
        got = parsed({**self.BASE, "invoice_date": "03/04/2026", "due_date": "04.05.2026"})
        self.assertEqual(got["invoice_date"], D(2026, 3, 4))

    def test_terms_still_work_in_the_due_slot(self):
        got = parsed({**self.BASE, "invoice_date": "14/03/2026", "due_date": "Upon receipt"})
        self.assertEqual(got["due_date"], D(2026, 3, 14))

    def test_a_word_where_the_due_date_goes_is_an_exception(self):
        raw = {**self.BASE, "supplier": "X", "invoice_number": "1", "invoice_date": "14/03/2026", "due_date": "TBC"}
        self.assertEqual(exception_reasons(parsed(raw)), ["unparsed_due_date"])


if __name__ == "__main__":
    unittest.main()
