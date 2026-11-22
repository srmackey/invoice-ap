"""Unpack the cross-border development set: sandbox/dev-crossborder/.

This is the first delivery of holdout-3, rejected as a holdout on 2026-11-21:
eight labels broke the rules, every continental European invoice was a scan
or a photo, and the images were 80 to 100 dpi. As development data those
faults matter less, and the low resolution is a fair stress test. It was built
by an agent outside this checkout, so it brings 22 layouts nobody here chose.

This script ran once against the delivered zip, which is not kept. It writes
the PDFs, keeps the agent's generator, notes, and labels as delivered under source/, points every
label at this folder, and drops the labels that break the brief's rules, by
check rather than by name: a due date with no due source or the reverse, or
exception reasons the exception rule does not give. The dropped ids are listed
in labels.json. Their PDFs stay, unlabeled.

Not frozen and not a benchmark.

Run from the checkout:
  python sandbox/make_dev_crossborder.py path/to/invoice_test_set.zip
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import zipfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "dev-crossborder"
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


def broken(row: dict) -> str | None:
    if (row["due_date"] is None) != (row["due_source"] is None):
        return "due date and due source disagree"
    if sorted(rule(row)) != sorted(row["exception_reasons"]):
        return "exception reasons differ from the rule"
    return None


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: make_dev_crossborder.py path/to/invoice_test_set.zip")
    zip_path = Path(sys.argv[1])
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zip_path) as z:
            for name in z.namelist():
                if ".." in name or name.startswith(("/", "\\")) or ":" in name:
                    raise SystemExit(f"unsafe path in zip: {name}")
            z.extractall(tmp)
        src = Path(tmp)
        for folder in ("clean", "scans", "photos"):
            (OUT / folder).mkdir(parents=True, exist_ok=True)
            for pdf in (src / folder).glob("*.pdf"):
                shutil.copy2(pdf, OUT / folder / pdf.name)
        (OUT / "source").mkdir(exist_ok=True)
        for name in ("make_set.py", "NOTES.md", "requirements.txt"):
            if (src / name).is_file():
                shutil.copy2(src / name, OUT / "source" / name)
        shutil.copy2(src / "labels.json", OUT / "source" / "labels.delivered.json")
        delivered = json.loads((src / "labels.json").read_text(encoding="utf-8"))
    rows, dropped = [], []
    for row in delivered["invoices"]:
        row = dict(row, file=row["file"].replace("holdout-3/", "dev-crossborder/", 1))
        reason = broken(row)
        if reason:
            dropped.append({"id": row["id"], "file": row["file"], "reason": reason})
        else:
            rows.append(row)
    out = {
        "set": "dev-crossborder",
        "note": "Development data: the rejected first delivery of holdout-3. Not frozen and not a benchmark.",
        "fields": delivered["fields"],
        "layouts": delivered.get("layouts"),
        "dropped": dropped,
        "invoices": rows,
    }
    (OUT / "labels.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"kept {len(rows)} labeled invoices, dropped {len(dropped)}: {[d['id'] for d in dropped]}")


if __name__ == "__main__":
    main()
