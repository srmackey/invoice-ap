"""The second holdout is the set its outside generator wrote, unchanged.

holdout-2/ was built by an agent with no access to this checkout, its runs,
or the first holdout, from holdout-2/BRIEF.md. Every file matches the sha256
in its label, so a hand edit or a regenerate shows up here. Labels are
rechecked against the exception rule and the terms rule in ../DESIGN.md with
an independent copy of both. The one-page check needs pymupdf
(sandbox/.venv) and skips without it.

Run from the checkout:
  python sandbox/test_holdout2.py
"""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
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


def terms_days(terms: str | None) -> int | None:
    """Days under the terms rule, or None where the rule leaves the due date missing."""
    if not terms:
        return None
    text = terms.lower()
    if re.search(r"\beom\b|end of (the )?month|\bprox", text):
        return None
    if re.search(r"\b(up)?on receipt\b", text):
        return 0
    found = re.search(r"\d+\s*%?\s*/\s*\d+\s*,?\s*n(et)?\s*/?\s*(\d+)", text)
    if found:
        return int(found.group(2))
    found = re.search(r"\bnet\s*/?\s*(\d+)\b", text)
    return int(found.group(1)) if found else None


class Holdout2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(LABELS.read_text(encoding="utf-8"))
        cls.rows = cls.payload["invoices"]

    def test_frozen(self):
        self.assertEqual(self.payload["frozen"], "2026-11-21")
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
