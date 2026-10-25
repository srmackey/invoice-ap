"""The command keeps one record after it exits.

Run from the checkout:
  python sandbox/test_read_record.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parent.parent

CHILD = r"""
import json
import sys
from pathlib import Path
from unittest.mock import patch
import read_invoice

pdf = Path(sys.argv[1])
dest = Path(sys.argv[2])
result = json.loads(sys.argv[3])
with patch.object(read_invoice, "READS_PATH", dest), patch.object(
    read_invoice, "read_invoice", return_value=result
):
    sys.argv = ["read_invoice.py", str(pdf)]
    read_invoice.main()
"""

SAMPLE = {
    "supplier": "Northline Supply Co.",
    "invoice_number": "NL-10442",
    "invoice_date": "2026-02-03",
    "due_date": "2026-03-05",
    "subtotal": "1260.00",
    "tax": "100.80",
    "total": "1360.80",
    "exception": False,
    "exception_reasons": [],
    "error": None,
}


def run_command(pdf: Path, dest: Path, result: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", CHILD, str(pdf), str(dest), json.dumps(result)],
        cwd=CHECKOUT,
        text=True,
        capture_output=True,
    )


class RecordTests(unittest.TestCase):
    def test_record_is_there_after_the_command_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "01-northline.pdf"
            pdf.write_bytes(b"%PDF-1.4")
            dest = root / "reads.jsonl"
            done = run_command(pdf, dest, SAMPLE)
            self.assertEqual(done.returncode, 0, done.stderr)
            line = json.loads(dest.read_text(encoding="utf-8").strip())
            self.assertEqual(line["file_name"], "01-northline.pdf")
            self.assertEqual(len(line["id"]), 32)
            self.assertTrue(line["read_at"].endswith("+00:00"))
            self.assertIsInstance(line["seconds"], float)
            self.assertEqual(line["seconds"], round(line["seconds"], 1))
            self.assertEqual(line["supplier"], "Northline Supply Co.")
            self.assertEqual(line["invoice_number"], "NL-10442")
            self.assertEqual(line["total"], "1360.80")
            self.assertIs(line["exception"], False)
            self.assertEqual(line["exception_reasons"], [])
            self.assertIsNone(line["error"])

    def test_a_tool_failure_is_the_error_on_that_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "01-northline.pdf"
            pdf.write_bytes(b"%PDF-1.4")
            dest = root / "reads.jsonl"
            failed = dict(SAMPLE)
            failed["supplier"] = None
            failed["exception"] = True
            failed["exception_reasons"] = ["missing_supplier"]
            failed["error"] = "docling request failed: refused"
            done = run_command(pdf, dest, failed)
            self.assertEqual(done.returncode, 1, done.stderr)
            line = json.loads(dest.read_text(encoding="utf-8").strip())
            self.assertEqual(line["error"], "docling request failed: refused")
            self.assertEqual(line["exception_reasons"], ["missing_supplier"])
            self.assertIs(line["exception"], True)


if __name__ == "__main__":
    unittest.main()
