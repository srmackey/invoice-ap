"""The score gate fails a build when a field or an exception is wrong.

Run from the checkout:
  python sandbox/test_score_gate.py
"""

from __future__ import annotations

import unittest

from score import enforce_gate, gate_misses


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


if __name__ == "__main__":
    unittest.main()
