"""The textract runner maps AnalyzeExpense summary fields to the seven.

No AWS call. The response below has the shape AnalyzeExpense returns.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from score import FIELDS, textract_fields


def field(kind: str, text: str, confidence: float = 99.0) -> dict:
    return {
        "Type": {"Text": kind, "Confidence": 99.0},
        "ValueDetection": {"Text": text, "Confidence": confidence},
    }


class TextractMap(unittest.TestCase):
    def test_seven_fields(self):
        response = {
            "ExpenseDocuments": [
                {
                    "SummaryFields": [
                        field("VENDOR_NAME", "Northline Supply Co."),
                        field("INVOICE_RECEIPT_ID", "NL-10442"),
                        field("INVOICE_RECEIPT_DATE", "02/03/2026"),
                        field("DUE_DATE", "03/05/2026"),
                        field("SUBTOTAL", "$1,260.00"),
                        field("TAX", "$100.80"),
                        field("TOTAL", "$1,360.80"),
                        field("ADDRESS", "12 Mill Road"),
                    ]
                }
            ]
        }
        got = textract_fields(response)
        self.assertEqual(set(got), set(FIELDS))
        self.assertEqual(got["supplier"], "Northline Supply Co.")
        self.assertEqual(got["total"], "$1,360.80")

    def test_missing_field_is_none(self):
        response = {"ExpenseDocuments": [{"SummaryFields": [field("TOTAL", "10.00")]}]}
        got = textract_fields(response)
        self.assertIsNone(got["due_date"])
        self.assertEqual(got["total"], "10.00")

    def test_highest_confidence_wins(self):
        response = {
            "ExpenseDocuments": [
                {
                    "SummaryFields": [
                        field("VENDOR_NAME", "Bill To Corp", 40.0),
                        field("VENDOR_NAME", "Northline Supply Co.", 95.0),
                    ]
                }
            ]
        }
        self.assertEqual(textract_fields(response)["supplier"], "Northline Supply Co.")

    def test_empty_response(self):
        got = textract_fields({})
        self.assertTrue(all(value is None for value in got.values()))


if __name__ == "__main__":
    unittest.main()
