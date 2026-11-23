"""The development sets for the reader-misses work are well formed.

Three sets, none frozen: practice/ (built from CLDR by make_practice.py),
inv-cdip-dev.json (INV-CDIP's multi-page invoices, page one), and
dev-crossborder/ (holdout-3's rejected first delivery). Their labels are
checked against the exception rule, including foreign_currency where the set
has currencies, and each due date set by terms against a day count printed in
the terms. INV-CDIP's pages are checked only when they have been fetched.

Run from the checkout:
  python sandbox/test_dev_sets.py
"""

from __future__ import annotations

import collections
import hashlib
import json
import re
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIELDS = ("supplier", "invoice_number", "invoice_date", "due_date", "subtotal", "tax", "total")


def rule(row: dict) -> list[str]:
    reasons = [] if row["currency"] == "USD" else ["foreign_currency"]
    reasons += [f"missing_{name}" for name in FIELDS if row[name] in (None, "")]
    if row["invoice_date"] and row["due_date"] and row["due_date"] < row["invoice_date"]:
        reasons.append("due_before_invoice")
    if all(row[name] not in (None, "") for name in ("subtotal", "tax", "total")):
        if abs(Decimal(row["subtotal"]) + Decimal(row["tax"]) - Decimal(row["total"])) > Decimal("0.01"):
            reasons.append("total_mismatch")
    return reasons


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class CurrencySets(unittest.TestCase):
    def check(self, rows: list[dict]):
        for row in rows:
            self.assertTrue((ROOT / row["file"]).is_file(), row["file"])
            self.assertEqual(sorted(rule(row)), sorted(row["exception_reasons"]), row["id"])
            self.assertEqual(bool(row["exception_reasons"]), row["exception"], row["id"])
            self.assertEqual(row["due_date"] is None, row["due_source"] is None, row["id"])
            if row["due_source"] == "terms":
                days = (date.fromisoformat(row["due_date"]) - date.fromisoformat(row["invoice_date"])).days
                printed = {int(n) for n in re.findall(r"\d+", row["terms"] or "")}
                self.assertTrue(days == 0 or days in printed, row["id"])

    def test_practice(self):
        payload = load(ROOT / "practice" / "labels.json")
        rows = payload["invoices"]
        self.assertEqual(len(rows), 40)
        self.check(rows)
        self.assertEqual({row["locale"] for row in rows},
                         {"en-US", "en-CA", "en-GB", "de-DE", "fr-FR", "nl-NL", "it-IT", "es-ES"})
        self.assertEqual({row["currency"] for row in rows}, {"USD", "CAD", "GBP", "EUR"})
        on_disk = {p.name for p in (ROOT / "practice").glob("*.pdf")}
        self.assertEqual(on_disk, {Path(row["file"]).name for row in rows})

    def test_practice_covers_what_the_work_needs(self):
        rows = load(ROOT / "practice" / "labels.json")["invoices"]
        kinds = collections.Counter()
        for row in rows:
            if row["date_style"] == "short" and row["country"] not in ("US", "CA") and int(row["invoice_date"][8:]) <= 12:
                kinds["ambiguous day-first"] += 1
            if row["terms"] and row["due_source"] is None and not row["due_printed_not_a_date"]:
                kinds["terms left for a person"] += 1
            if row["due_printed_not_a_date"]:
                kinds["due date not a date"] += 1
            if "," in row["number_style"][-3:]:
                kinds["decimal comma"] += 1
        for kind in ("ambiguous day-first", "terms left for a person", "due date not a date", "decimal comma"):
            self.assertGreaterEqual(kinds[kind], 3, kind)

    def test_dev_crossborder(self):
        payload = load(ROOT / "dev-crossborder" / "labels.json")
        self.check(payload["invoices"])
        self.assertEqual(len(payload["invoices"]) + len(payload["dropped"]), 60)


class InvCdipDev(unittest.TestCase):
    def setUp(self):
        if not (ROOT / "inv-cdip-dev.json").is_file() or not (ROOT / "inv-cdip.json").is_file():
            self.skipTest("run get_inv_cdip.py, then make_inv_cdip_dev.py")

    def test_labels(self):
        payload = load(ROOT / "inv-cdip-dev.json")
        rows = payload["invoices"]
        self.assertEqual(len(rows) + len(payload["no_usable_labels"]), 74)
        frozen_ids = {row["id"] for row in load(ROOT / "inv-cdip.json")["invoices"]}
        self.assertFalse(frozen_ids & {row["id"] for row in rows})
        for row in rows:
            self.assertTrue(row["labeled"], row["id"])
            self.assertTrue(row["file"].startswith("inv-cdip/dev/"), row["id"])

    def test_pages_when_fetched(self):
        rows = load(ROOT / "inv-cdip-dev.json")["invoices"]
        present = [row for row in rows if (ROOT / row["file"]).is_file()]
        if not present:
            self.skipTest("run get_inv_cdip.py, then make_inv_cdip_dev.py")
        for row in present:
            self.assertEqual(hashlib.sha256((ROOT / row["file"]).read_bytes()).hexdigest(), row["sha256"], row["id"])


if __name__ == "__main__":
    unittest.main()
