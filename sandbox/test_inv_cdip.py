"""INV-CDIP's labels are the ones get_inv_cdip.py wrote, unchanged.

The label file is frozen and stays out of git, since it carries INV-CDIP's
annotations; get_inv_cdip.py rebuilds it. When it is present, its hash must be
the frozen one and its shape is checked here: one-page invoices only, each
judged on the fields it lists, values in this project's formats, attribution
present. Without it those checks skip. When the documents have been fetched into
inv-cdip/, each one must match the sha256 in its label; without them those
checks skip, since the documents are never committed. The date and money
rules are checked on printed forms seen in the set, with no network call.

Run from the checkout:
  python sandbox/test_inv_cdip.py
"""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LABELS = ROOT / "inv-cdip.json"
FIVE = ("invoice_number", "invoice_date", "due_date", "tax", "total")

try:
    import get_inv_cdip
except ImportError:  # pymupdf missing outside sandbox/.venv
    get_inv_cdip = None


class FrozenLabels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not LABELS.is_file():
            raise unittest.SkipTest("labels not built; run get_inv_cdip.py")
        cls.data = json.loads(LABELS.read_text(encoding="utf-8"))
        cls.rows = cls.data["invoices"]

    @unittest.skipIf(get_inv_cdip is None, "needs sandbox/.venv (pymupdf)")
    def test_the_file_is_the_frozen_one(self):
        self.assertEqual(get_inv_cdip.labels_sha256(LABELS), get_inv_cdip.FROZEN_SHA256)

    def test_the_set_says_what_it_labels(self):
        self.assertEqual(self.data["fields"], list(FIVE))
        self.assertIs(self.data["exception_labeled"], False)
        self.assertEqual(self.data["frozen"], "2026-11-21")
        self.assertIn("CC BY-NC 4.0", self.data["attribution"])
        self.assertIn("salesforce", self.data["attribution"])

    def test_counts(self):
        self.assertEqual(len(self.rows), 276)
        self.assertEqual(len(self.data["excluded"]), 74)
        self.assertEqual(len(self.rows) + len(self.data["excluded"]), 350)
        self.assertTrue(all(item["pages"] > 1 for item in self.data["excluded"]))

    def test_each_row_is_judged_on_what_it_labels(self):
        for row in self.rows:
            with self.subTest(row["id"]):
                self.assertTrue(row["labeled"])
                self.assertEqual(row["labeled"], [n for n in FIVE if n in row["labeled"]])
                for name in FIVE:
                    self.assertEqual(name in row, name in row["labeled"])
                self.assertIsNone(row["exception"])
                self.assertEqual(row["file"], f"inv-cdip/docs/{row['id']}.pdf")
                self.assertEqual(set(row["printed"]), set(row["labeled"]))

    def test_values_are_in_project_formats(self):
        money = re.compile(r"\d+\.\d\d")
        for row in self.rows:
            with self.subTest(row["id"]):
                for name in ("invoice_date", "due_date"):
                    if name in row:
                        date.fromisoformat(row[name])
                for name in ("tax", "total"):
                    if name in row:
                        self.assertRegex(row[name], money)
                if "total" in row:
                    self.assertIn(row["total_from"], ("total_amount", "amount_due"))
                if "invoice_number" in row:
                    self.assertEqual(row["invoice_number"], " ".join(row["invoice_number"].split()))

    def test_documents_match_their_pins(self):
        docs = ROOT / "inv-cdip" / "docs"
        if not docs.is_dir():
            self.skipTest("documents not fetched; run get_inv_cdip.py")
        for row in self.rows:
            with self.subTest(row["id"]):
                data = (ROOT / row["file"]).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), row["sha256"])


@unittest.skipIf(get_inv_cdip is None, "needs sandbox/.venv (pymupdf)")
class LabelRules(unittest.TestCase):
    def test_dates(self):
        cases = {
            "4/10/2000": "2000-04-10",
            "1/11/91": "1991-01-11",
            "03-14-91": "1991-03-14",
            "6/4/02": "2002-06-04",
            "JUNE 1, 1997": "1997-06-01",
            "Jan. 5, 1990": "1990-01-05",
            "12 Mar 91": "1991-03-12",
            "03-Mar-91": "1991-03-03",
            "MAR25/85": "1985-03-25",
            "13/04/91": "1991-04-13",
            "3/4/'99": "1999-03-04",
        }
        for printed, iso in cases.items():
            with self.subTest(printed):
                self.assertEqual(get_inv_cdip.read_date(printed), iso)
        for printed in ("12315", "2/30/91", "Smarch 3, 1991"):
            with self.subTest(printed):
                self.assertIsNone(get_inv_cdip.read_date(printed))

    def test_money(self):
        cases = {"$2,000.00": "2000.00", "$3,000.": "3000.00", ".00": "0.00",
                 "48.1": "48.10", "$4, 200.00": "4200.00", "1 420.02": "1420.02"}
        for printed, value in cases.items():
            with self.subTest(printed):
                self.assertEqual(get_inv_cdip.read_money(printed), value)
        for printed in ("8.554", "447.34CR", "(861,850.00)", "$15.769.00", "10.00T", "$"):
            with self.subTest(printed):
                self.assertIsNone(get_inv_cdip.read_money(printed))

    def annotation(self, *fields):
        return {"Fields": [{"value": {"label": label, "tag": tag}} for label, tag in fields]}

    def test_total_prefers_total_amount_then_amount_due(self):
        doc = {"id": "x", "sha256": "0"}
        both, _ = get_inv_cdip.label_row(
            doc, self.annotation(("total_amount", "$100.00"), ("amount_due", "$90.00"))
        )
        self.assertEqual((both["total"], both["total_from"]), ("100.00", "total_amount"))
        due, _ = get_inv_cdip.label_row(doc, self.annotation(("amount_due", "$90.00")))
        self.assertEqual((due["total"], due["total_from"]), ("90.00", "amount_due"))

    def test_unreadable_and_conflicting_values_are_dropped(self):
        doc = {"id": "x", "sha256": "0"}
        row, dropped = get_inv_cdip.label_row(
            doc,
            self.annotation(
                ("Invoice_date", "12315"), ("Invoice_date", "2/26/98"),
                ("total_amount", "447.34CR"), ("Invoice_number", "F165 1355"),
                ("Purchase_order", "P072138"),
            ),
        )
        self.assertEqual(row["labeled"], ["invoice_number"])
        self.assertEqual(row["invoice_number"], "F165 1355")
        self.assertEqual(sorted(item["field"] for item in dropped), ["invoice_date", "total"])


if __name__ == "__main__":
    unittest.main()
