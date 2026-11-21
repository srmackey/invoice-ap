"""The generated holdout is the set make_holdout.py wrote, unchanged.

Every file matches the sha256 in its label, so a hand edit or a regenerate
shows up here. The shape the spec asks for is checked from the labels:
about a hundred invoices, at least ten layouts, about a quarter
exceptions across all three kinds, and clean, scanned, and photographed
pages in every layout. Labels are rechecked against the exception rule in
../DESIGN.md with an independent copy of that rule. The one-page check
needs pymupdf (sandbox/.venv) and skips without it.

Run from the checkout:
  python sandbox/test_holdout.py
"""

from __future__ import annotations

import collections
import hashlib
import json
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LABELS = ROOT / "holdout" / "labels.json"
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


class Holdout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(LABELS.read_text(encoding="utf-8"))
        cls.rows = cls.payload["invoices"]

    def test_files_are_unchanged(self):
        for row in self.rows:
            path = ROOT / row["file"]
            self.assertTrue(path.is_file(), row["file"])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), row["sha256"], row["file"])

    def test_no_stray_files(self):
        listed = {row["file"] for row in self.rows}
        on_disk = {p.relative_to(ROOT).as_posix() for p in (ROOT / "holdout").rglob("*.pdf")}
        self.assertEqual(on_disk, listed)

    def test_size_and_layouts(self):
        self.assertEqual(len(self.rows), 100)
        self.assertEqual(len({row["id"] for row in self.rows}), 100)
        layouts = {row["layout"] for row in self.rows}
        self.assertGreaterEqual(len(layouts), 10)
        self.assertEqual(layouts, set(self.payload["layouts"]))
        self.assertEqual(self.payload["fields"], list(FIELDS))

    def test_every_layout_in_every_render(self):
        seen = collections.defaultdict(set)
        for row in self.rows:
            seen[row["layout"]].add(row["render"])
        for layout, renders in seen.items():
            self.assertEqual(renders, {"clean", "scan", "photo"}, layout)

    def test_exceptions(self):
        exceptions = [row for row in self.rows if row["exception"]]
        self.assertTrue(20 <= len(exceptions) <= 30, len(exceptions))
        kinds = {reason for row in exceptions for reason in row["exception_reasons"]}
        self.assertIn("due_before_invoice", kinds)
        self.assertIn("total_mismatch", kinds)
        self.assertTrue(any(kind.startswith("missing_") for kind in kinds), kinds)

    def test_labels_follow_the_rule(self):
        for row in self.rows:
            self.assertEqual(rule(row), row["exception_reasons"], row["id"])
            self.assertEqual(bool(row["exception_reasons"]), row["exception"], row["id"])

    def test_numeric_day_first_is_unambiguous(self):
        for row in self.rows:
            if row["layout"] != "day-first":
                continue
            self.assertGreater(int(row["invoice_date"][8:]), 12, row["id"])
            if row["due_source"] == "printed":
                self.assertGreater(int(row["due_date"][8:]), 12, row["id"])

    def test_due_dates_from_terms(self):
        """../DESIGN.md: terms set an unprinted due date; ones the rule cannot compute leave it missing."""
        from datetime import date, timedelta

        forms = set()
        for row in self.rows:
            if row["due_source"] != "terms" and not (row["terms"] and row["due_date"] is None):
                continue
            terms = row["terms"]
            forms.add(terms.split()[0] if terms.startswith("Net") and "EOM" not in terms else terms)
            issued = date.fromisoformat(row["invoice_date"])
            if terms == "Due on receipt":
                want = issued
            elif terms == "2/10 Net 30":
                want = issued + timedelta(days=30)
            elif terms.endswith("EOM"):
                want = None
            else:
                want = issued + timedelta(days=int(terms.split()[1]))
            got = date.fromisoformat(row["due_date"]) if row["due_date"] else None
            self.assertEqual(got, want, row["id"])
        self.assertEqual(forms, {"Net", "Due on receipt", "2/10 Net 30", "Net 30 EOM"})

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
