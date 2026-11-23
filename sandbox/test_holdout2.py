"""The second holdout is the set its outside generator wrote, relabeled once.

holdout-2/ was built by an agent with no access to this checkout, its runs,
or the first holdout, from holdout-2/BRIEF.md. Every file matches the sha256
in its label, so a hand edit or a regenerate shows up here. The PDFs are as
delivered. The labels were delivered under the narrow terms rule and were
relabeled on 2026-11-22 to the wider rule in ../DESIGN.md by
relabel_holdout2.py, which holds a copy of that rule written independently of
the reader. Labels are rechecked against the exception rule with this file's
own copy and against the terms rule with that one, and the terms copy is
checked here on the examples in DESIGN.md and holdout-3/BRIEF.md. The
one-page check needs pymupdf (sandbox/.venv) and skips without it.

Run from the checkout:
  python sandbox/test_holdout2.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from relabel_holdout2 import RELABELED, TERMS_RULE, terms_days  # noqa: E402

SET = ROOT / "holdout-2"
LABELS = SET / "labels.json"
FIELDS = ("supplier", "invoice_number", "invoice_date", "due_date", "subtotal", "tax", "total")
CENT = Decimal("0.01")


def rule(row: dict) -> list[str]:
    reasons = [f"missing_{name}" for name in FIELDS if row[name] in (None, "")]
    if row["invoice_date"] and row["due_date"] and row["due_date"] < row["invoice_date"]:
        reasons.append("due_before_invoice")
    if all(row[name] not in (None, "") for name in ("subtotal", "tax", "total")):
        if abs(Decimal(row["subtotal"]) + Decimal(row["tax"]) - Decimal(row["total"])) > CENT:
            reasons.append("total_mismatch")
    return reasons


class WiderTermsCopy(unittest.TestCase):
    """The independent copy follows the wider rule on its published examples."""

    def check(self, cases: dict):
        for text, days in cases.items():
            self.assertEqual(terms_days(text), days, text)

    def test_one_period(self):
        self.check({
            "Net 30": 30, "NET 45 DAYS": 45, "net15": 15, "Terms: Net 10": 10, "Net/30": 30,
            "30 days net": 30, "30 days": 30, "Payment terms: 30 days": 30, "Payment due within 14 days": 14,
            "Payable within 30 days": 30, "Payment due in 30 days": 30, "Due: 30 days": 30,
            "Net 30 days from invoice date": 30, "Net 30. Late payments 1.5% per month": 30,
        })

    def test_one_period_in_five_more_languages(self):
        self.check({
            "30 jours net": 30, "à 45 jours": 45, "30 Tage netto": 30, "zahlbar innerhalb 14 Tagen": 14,
            "netto 30 dagen": 30, "binnen 14 dagen": 14, "a 30 giorni": 30, "60 giorni netto": 60,
            "a 30 días": 30, "neto 60 días": 60,
        })

    def test_receipt(self):
        self.check({text: 0 for text in (
            "Due on receipt", "DUE UPON RECEIPT", "Payable on receipt", "Upon Receipt", "Due immediately",
            "sofort zahlbar", "à réception", "bij ontvangst", "al ricevimento", "a la recepción",
            "Due on receipt; 1.5% per month on late balances",
        )})

    def test_discount_terms_take_the_net_period(self):
        self.check({text: 30 for text in (
            "2/10 Net 30", "2% 10, Net 30", "2/10 n/30", "1/15 Net 30 days", "2% 10 days, net 30",
            "2 % Skonto innerhalb 10 Tagen, 30 Tage netto", "2 % Skonto innerhalb 14 Tagen, 30 Tage netto",
            "escompte 2 % à 10 jours, net à 30 jours",
        )})

    def test_left_null(self):
        self.check({text: None for text in (
            "Net 30 EOM", "2/10 EOM", "Net 30 end of month", "10th prox", "Net 30 E.O.M.", "30 days end of month",
            "30 jours fin de mois", "30 Tage zum Monatsende", "30 dagen einde maand", "30 giorni fine mese",
            "30 días fin de mes", "30 days from delivery", "14 Tage nach Lieferung", "Net 30 from statement",
            "Due on receipt of goods", "30 days from receipt of invoice", "2% 10 days", "30 or 60 days",
            "30-60 Tage", "Net 200", "COD", "Thank you", "Net amount 30.00", "Net 1,200.00",
            "Payment by bank transfer", "Payment by direct deposit", "", None,
        )})


class Holdout2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(LABELS.read_text(encoding="utf-8"))
        cls.rows = cls.payload["invoices"]

    def test_frozen(self):
        self.assertEqual(self.payload["frozen"], "2026-11-21")
        self.assertEqual(self.payload["relabeled"], RELABELED)
        self.assertEqual(self.payload["terms_rule"], TERMS_RULE)
        self.assertEqual(self.payload["fields"], list(FIELDS))

    def test_files_are_unchanged(self):
        for row in self.rows:
            path = ROOT / row["file"]
            self.assertTrue(path.is_file(), row["file"])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), row["sha256"], row["file"])

    def test_no_stray_files(self):
        listed = {row["file"] for row in self.rows}
        on_disk = {p.relative_to(ROOT).as_posix() for p in SET.rglob("*.pdf")}
        self.assertEqual(on_disk, listed)

    def test_size_and_renders(self):
        self.assertEqual(len(self.rows), 100)
        self.assertEqual(len({row["id"] for row in self.rows}), 100)
        self.assertEqual({row["layout"] for row in self.rows}, set(self.payload["layouts"]))
        folders = {"clean": "/clean/", "scan": "/scans/", "photo": "/photos/"}
        for row in self.rows:
            self.assertIn(folders[row["render"]], row["file"], row["id"])

    def test_labels_follow_the_exception_rule(self):
        for row in self.rows:
            self.assertEqual(sorted(rule(row)), sorted(row["exception_reasons"]), row["id"])
            self.assertEqual(bool(row["exception_reasons"]), row["exception"], row["id"])

    def test_due_dates_from_terms(self):
        for row in self.rows:
            self.assertIn(row["due_source"], ("printed", "terms", None), row["id"])
            self.assertEqual(row["due_date"] is None, row["due_source"] is None, row["id"])
            if row["due_source"] == "printed":
                continue
            days = terms_days(row["terms"])
            if row["due_source"] == "terms":
                want = date.fromisoformat(row["invoice_date"]) + timedelta(days=days)
                self.assertEqual(date.fromisoformat(row["due_date"]), want, row["id"])
            elif row["invoice_date"]:
                self.assertIsNone(days, row["id"])

    def test_one_page_each(self):
        try:
            import pymupdf
        except ImportError:
            self.skipTest("pymupdf is in sandbox/.venv")
        for row in self.rows:
            with pymupdf.open(ROOT / row["file"]) as doc:
                self.assertEqual(doc.page_count, 1, row["file"])


if __name__ == "__main__":
    unittest.main()
