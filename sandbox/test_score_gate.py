"""The score gate fails a build when a field or an exception is wrong.

Run from the checkout:
  python sandbox/test_score_gate.py
"""

from __future__ import annotations

import unittest

from score import enforce_gate, gate_misses, invoice_scores, summarize


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


if __name__ == "__main__":
    unittest.main()
