"""Write the INV-CDIP development set: the 74 multi-page invoices, page one only.

INV-CDIP's frozen set (inv-cdip.json) holds its 276 one-page invoices. The 74
with more than one page were left out of it. They are real, labeled, and in no
frozen set, so they are development data for the reader-misses work: real
printed dates nobody chose.

Each annotation sits on one page image whose shape matches the document's
first page, so the labels are read as page-one labels. This script writes
each first page as its own PDF under inv-cdip/dev/ (git ignores inv-cdip/) and
writes inv-cdip-dev.json with the same rules as inv-cdip.json, by calling
get_inv_cdip.py's own label_row. Supplier and subtotal are not labeled, so the
exception is not judged.

This is development data, not a benchmark. It may be regenerated. Because it
comes from the same archive as the frozen INV-CDIP set, a later INV-CDIP score
is no longer a clean holdout once the reader is developed against this.

The annotations are CC BY-NC 4.0 (Salesforce Research); attribution is in
inv-cdip.json and carried into the output.

Needs sandbox/.venv (pymupdf) and the documents fetched by get_inv_cdip.py.

Run from the checkout:
  python sandbox/make_inv_cdip_dev.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import get_inv_cdip  # noqa: E402

SOURCE = ROOT / "inv-cdip"
OUT = SOURCE / "dev"
LABELS = ROOT / "inv-cdip-dev.json"


def page_one(doc_id: str) -> Path:
    OUT.mkdir(exist_ok=True)
    target = OUT / f"{doc_id}.pdf"
    with pymupdf.open(SOURCE / "docs" / f"{doc_id}.pdf") as source:
        first = pymupdf.open()
        first.insert_pdf(source, from_page=0, to_page=0)
        first.save(target, garbage=3, deflate=True)
    return target


def main() -> None:
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    frozen = json.loads((ROOT / "inv-cdip.json").read_text(encoding="utf-8"))
    rows, dropped, unlabeled = [], [], []
    for doc in manifest["documents"]:
        if (doc["pages"] or 0) <= 1:
            continue
        annotation = json.loads((SOURCE / "annotation" / f"{doc['id']}.json").read_text(encoding="utf-8"))
        target = page_one(doc["id"])
        row, lost = get_inv_cdip.label_row(doc, annotation)
        row["file"] = f"inv-cdip/dev/{doc['id']}.pdf"
        row["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        row["source_pages"] = doc["pages"]
        dropped += lost
        if row["labeled"]:
            rows.append(row)
        else:
            unlabeled.append(doc["id"])
    out = {
        "set": "inv-cdip-dev",
        "note": "Development data: INV-CDIP's multi-page invoices, page one only. Not frozen and not a benchmark.",
        "generator": "make_inv_cdip_dev.py",
        "source": manifest["source"],
        "commit": manifest["commit"],
        "attribution": frozen["attribution"],
        "fields": list(get_inv_cdip.LABELED),
        "exception_labeled": False,
        "no_usable_labels": unlabeled,
        "dropped": dropped,
        "invoices": rows,
    }
    LABELS.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    counts = {name: sum(name in row["labeled"] for row in rows) for name in get_inv_cdip.LABELED}
    print(f"labeled {len(rows)} invoices, no usable labels {len(unlabeled)}, dropped values {len(dropped)}, per field {counts}")


if __name__ == "__main__":
    main()
