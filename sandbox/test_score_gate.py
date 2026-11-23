"""The score gate fails a build when a field or an exception is wrong.

Run from the checkout:
  python sandbox/test_score_gate.py
"""

from __future__ import annotations

import unittest

from score import enforce_gate, gate_misses, invoice_scores, judged, summarize


def row(invoice_id: str, correct: int = 7, exception: bool = False, label: bool = False) -> dict:
    return {
        "id": invoice_id,
        "correct_fields": correct,
        "exception": exception,
        "label_exception": label,
    }


class GateMissTests(unittest.TestCase):
    def test_pass_when_fields_and_exception_match(self):
        self.assertEqual(
            gate_misses([row("01-northline"), row("08-westwind", exception=True, label=True)]),
            [],
        )

    def test_field_miss(self):
        self.assertEqual(gate_misses([row("odd-lumen", correct=2)]), ["odd-lumen 2/7"])

    def test_exception_miss(self):
        self.assertEqual(
            gate_misses([row("03-keel-electric", exception=True, label=False)]),
            ["03-keel-electric exception=True label=False"],
        )

    def test_both_on_one_line(self):
        self.assertEqual(
            gate_misses([row("photo-04-brightwater", correct=4, exception=True, label=False)]),
            ["photo-04-brightwater 4/7, exception=True label=False"],
        )

    def test_empty_run_fails(self):
        self.assertEqual(gate_misses([]), ["no invoices"])


class EnforceGateTests(unittest.TestCase):
    def test_pass_prints_and_returns(self):
        enforce_gate(
            {
                "field_accuracy": "7/7",
                "straight_through": "1/1",
                "results": [row("01-northline")],
            }
        )

    def test_miss_exits(self):
        with self.assertRaises(SystemExit) as caught:
            enforce_gate(
                {
                    "field_accuracy": "6/7",
                    "straight_through": "0/1",
                    "results": [row("01-northline", correct=6)],
                }
            )
        self.assertEqual(caught.exception.code, 1)


class InvoiceScoreTests(unittest.TestCase):
    def test_an_invoice_counts_only_when_every_field_is_right(self):
        rows = [row("01-northline"), row("02-harbor", correct=6, exception=True), row("03-keel")]
        self.assertEqual(invoice_scores(rows), ("2/3", []))

    def test_a_wrong_field_with_no_exception_is_a_silent_error(self):
        rows = [row("01-northline", correct=6), row("08-westwind", correct=5, exception=True)]
        self.assertEqual(invoice_scores(rows), ("0/2", ["01-northline"]))

    def test_a_set_that_labels_fewer_fields_scores_over_those(self):
        partial = dict(row("cdip-1", correct=5), right={name: True for name in "abcde"})
        report = summarize("textract", [partial], 1.0)
        self.assertEqual(report["field_accuracy"], "5/5")
        self.assertEqual(report["invoice_accuracy"], "1/1")
        self.assertEqual(report["silent_errors"], [])


class DeclaredFieldTests(unittest.TestCase):
    """A label row that lists its fields is judged on those fields only."""

    READ = {
        "supplier": "Somebody Else Inc",
        "invoice_number": "2750",
        "invoice_date": "2000-04-10",
        "due_date": "2000-05-10",
        "subtotal": "1.00",
        "tax": None,
        "total": "2000.00",
    }

    def partial(self, **extra) -> dict:
        base = {
            "id": "lkxd0060",
            "invoice_number": "2750",
            "invoice_date": "2000-04-10",
            "total": "2000.00",
            "labeled": ["invoice_number", "invoice_date", "total"],
            "exception": None,
        }
        base.update(extra)
        return base

    def test_only_labeled_fields_are_judged(self):
        item = judged(self.partial(), self.READ, "textract", 1.0, None)
        self.assertEqual(sorted(item["right"]), ["invoice_date", "invoice_number", "total"])
        self.assertEqual(item["correct_fields"], 3)
        report = summarize("textract", [item], 1.0)
        self.assertEqual(report["field_accuracy"], "3/3")
        self.assertEqual(report["invoice_accuracy"], "1/1")

    def test_a_wrong_labeled_field_still_counts(self):
        item = judged(self.partial(total="1999.00"), self.READ, "textract", 1.0, None)
        self.assertEqual(item["correct_fields"], 2)
        self.assertEqual(summarize("textract", [item], 1.0)["field_accuracy"], "2/3")

    def test_a_missing_read_of_a_labeled_field_is_wrong(self):
        item = judged(self.partial(), None, "textract", 1.0, "no read")
        self.assertEqual(item["correct_fields"], 0)
        self.assertEqual(summarize("textract", [item], 1.0)["field_accuracy"], "0/3")

    def test_a_row_without_labeled_is_judged_on_all_seven(self):
        full = dict(self.READ, id="01-northline", exception=False)
        item = judged(full, self.READ, "textract", 1.0, None)
        self.assertEqual(len(item["right"]), 7)

    def test_an_unknown_field_is_refused(self):
        with self.assertRaises(ValueError):
            judged(self.partial(labeled=["purchase_order"]), self.READ, "textract", 1.0, None)

    def test_the_gate_skips_an_unlabeled_exception(self):
        item = judged(self.partial(), self.READ, "textract", 1.0, None)
        self.assertEqual(gate_misses([item]), [])


class PaidRunName(unittest.TestCase):
    """A paid run's file name is settled before any read."""

    def setUp(self):
        import tempfile
        from datetime import date
        from pathlib import Path
        from unittest import mock

        import score

        self.score = score
        self.today = date.today().isoformat()
        self.dir = tempfile.TemporaryDirectory()
        self.runs = Path(self.dir.name)
        patch = mock.patch.object(score, "RUNS", self.runs)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.dir.cleanup)
        self.hard = score.ROOT / "hard" / "labels.json"
        self.holdout = score.ROOT / "holdout-3" / "labels.json"

    def test_a_first_run_takes_the_plain_name(self):
        name = self.score.paid_run_name("hosted", self.hard, {})
        self.assertEqual(name.name, f"{self.today}-hard-hosted.json")

    def test_a_second_run_on_a_regression_set_is_numbered(self):
        (self.runs / f"{self.today}-hard-hosted.json").write_text("{}")
        (self.runs / f"{self.today}-hard-hosted-2.json").write_text("{}")
        name = self.score.paid_run_name("hosted", self.hard, {})
        self.assertEqual(name.name, f"{self.today}-hard-hosted-3.json")

    def test_a_second_run_on_a_holdout_is_refused_before_reading(self):
        (self.runs / f"{self.today}-holdout-3-hosted.json").write_text("{}")
        with self.assertRaises(SystemExit) as refused:
            self.score.paid_run_name("hosted", self.holdout, {"frozen": "2026-11-22"})
        self.assertIn("nothing was read", str(refused.exception))

    def test_a_frozen_set_outside_a_holdout_folder_is_refused_too(self):
        (self.runs / f"{self.today}-inv-cdip-textract.json").write_text("{}")
        with self.assertRaises(SystemExit):
            self.score.paid_run_name("textract", self.score.ROOT / "inv-cdip.json", {"frozen": "2026-11-21"})


if __name__ == "__main__":
    unittest.main()
