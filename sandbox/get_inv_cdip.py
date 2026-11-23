"""Fetch INV-CDIP's labeled invoices into inv-cdip/, which git ignores.

INV-CDIP is 350 real invoices from the UCSF Industry Documents Library
tobacco collections, labeled by Salesforce Research:

    Mingfei Gao, Zeyuan Chen, Nikhil Naik, Kazuma Hashimoto, Caiming Xiong,
    Ran Xu. "Field Extraction from Forms with Unlabeled Data."
    ACL Spa-NLP Workshop, 2022. https://github.com/salesforce/inv-cdip

The annotations are copyright 2022 salesforce.com, inc., licensed CC BY-NC
4.0 (https://creativecommons.org/licenses/by-nc/4.0/). The documents belong
to the companies that made them; the library offers them under fair use.
Neither is committed here. This script fetches both for local scoring.

The repository is archived and its own download script has a placeholder
host, so the PDFs come from the bucket the library's viewer reads. The
annotation repository is pinned to one commit.

Re-running skips files already present. The manifest records each file's
sha256 and page count, so multi-page documents can be counted and left out.

Then, once, it writes inv-cdip.json: labels in this project's format for the
one-page invoices, pinned by sha256. The set is frozen: the script refuses to
write the file again, and FROZEN_SHA256 below is the hash of the file frozen on
2026-11-21, so a rebuilt copy is checked against it. The file stays out of git
because it carries INV-CDIP's annotations. The rules:

- Five fields can be labeled: invoice_number, invoice_date, due_date, tax,
  total. Supplier and subtotal never are, so the exception rule is not
  judged on this set.
- Each invoice is judged only on the fields annotated for it, listed in its
  "labeled". A field with no annotation is unlabeled, not absent: INV-CDIP
  labels only printed due dates, and the reader computes some from terms.
- total is total_amount, else amount_due. "total_from" says which.
- Dates are as printed, read month first (these are US documents) unless
  the first number is above 12. Two-digit years below 30 are 20xx, the
  rest 19xx. Money drops "$", commas, and spaces.
- A value that does not read cleanly as a date or an amount (credits,
  "CR", a stray letter, three decimals, two decimal points) is left
  unlabeled and listed under "dropped" with its printed text. So is a
  field annotated twice with different values.
- Multi-page documents are listed under "excluded", not labeled.

Needs sandbox/.venv (pymupdf).
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "inv-cdip"
LABELS = ROOT / "inv-cdip.json"
FROZEN = "2026-11-21"
# sha256 of inv-cdip.json as frozen, with CRLF line endings read as LF.
FROZEN_SHA256 = "8b7d46168761a84031dd0f811fd32de5188c0d0c05f109cbea19b1109abe8a31"
LABELED = ("invoice_number", "invoice_date", "due_date", "tax", "total")
SOURCE_LABELS = {
    "Invoice_number": "invoice_number",
    "Invoice_date": "invoice_date",
    "due_date": "due_date",
    "total_tax_amount": "tax",
    "total_amount": "total",
    "amount_due": "total",
}
MONTHS = {
    name: n
    for n, names in enumerate(
        [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
         ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
         ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
         ("dec", "december")],
        1,
    )
    for name in names
}
ATTRIBUTION = (
    "Labels adapted from INV-CDIP (https://github.com/salesforce/inv-cdip), "
    "copyright 2022 salesforce.com, inc., CC BY-NC 4.0 "
    "(https://creativecommons.org/licenses/by-nc/4.0/), provided as is. "
    "Changed: dates and amounts normalized, fields renamed, multi-page "
    "documents and unreadable values left out. Gao, Chen, Naik, Hashimoto, "
    "Xiong, Xu, 'Field Extraction from Forms with Unlabeled Data', ACL "
    "Spa-NLP Workshop 2022. Documents: UCSF Industry Documents Library, "
    "not redistributed."
)
COMMIT = "80a7c2cfb6838f4a02d04257444b528837d6fda9"
REPO = f"https://raw.githubusercontent.com/salesforce/inv-cdip/{COMMIT}"
PDF_ROOT = "https://ucsf-idl-dataset.s3.us-east-1.amazonaws.com"
AGENT = "invoice-ap-sandbox/0.1 (research scoring)"
PAUSE = 0.2


def get(url: str, tries: int = 3) -> bytes:
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as err:
            if err.code == 404:
                raise
            if attempt == tries - 1:
                raise
        except urllib.error.URLError:
            if attempt == tries - 1:
                raise
        time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def pdf_url(doc_id: str) -> str:
    a, b, c, d = doc_id[:4]
    return f"{PDF_ROOT}/{a}/{b}/{c}/{d}/{doc_id}/{doc_id}.pdf"


def fetch(path: Path, url: str) -> bytes | None:
    if path.exists():
        return path.read_bytes()
    try:
        data = get(url)
    except urllib.error.HTTPError as err:
        print(f"  {err.code} {url}", file=sys.stderr)
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    time.sleep(PAUSE)
    return data


def pages(data: bytes) -> int | None:
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            return doc.page_count
    except Exception:
        return None


def year(text: str) -> int:
    n = int(text.lstrip("'"))
    if len(text.lstrip("'")) == 2:
        return 2000 + n if n < 30 else 1900 + n
    return n


def read_date(text: str) -> str | None:
    """ISO date from a printed date, or None when it does not read cleanly."""
    t = " ".join(text.replace(",", " , ").split()).strip()
    words = re.fullmatch(r"([A-Za-z]+)\.?\s*(\d{1,2})\s*[,./]?\s*('?\d{2}|\d{4})", t)
    if words:
        month, day, yr = MONTHS.get(words[1].lower()), int(words[2]), year(words[3])
    else:
        words = re.fullmatch(r"(\d{1,2})[\s-]+([A-Za-z]+)\.?[\s,-]+('?\d{2}|\d{4})", t)
        if words:
            day, month, yr = int(words[1]), MONTHS.get(words[2].lower()), year(words[3])
        else:
            nums = re.fullmatch(r"(\d{1,2})\s*([/.\- ])\s*(\d{1,2})\s*\2\s*('?\d{2}|\d{4})", t)
            if not nums:
                return None
            first, second, yr = int(nums[1]), int(nums[3]), year(nums[4])
            month, day = (second, first) if first > 12 else (first, second)
    if month is None:
        return None
    try:
        return date(yr, month, day).isoformat()
    except ValueError:
        return None


def read_money(text: str) -> str | None:
    """Decimal dollars from a printed amount, or None when it does not read cleanly."""
    t = re.sub(r"[\s$,]", "", text)
    if not re.fullmatch(r"\d*(\.\d{0,2})?", t) or not re.search(r"\d", t):
        return None
    try:
        return str(Decimal(t).quantize(Decimal("0.01")))
    except InvalidOperation:
        return None


def read_value(field: str, printed: str) -> str | None:
    if field in ("invoice_date", "due_date"):
        return read_date(printed)
    if field in ("tax", "total"):
        return read_money(printed)
    text = " ".join(printed.split())
    return text or None


def label_row(doc: dict, annotation: dict) -> tuple[dict, list[dict]]:
    """One invoice's labels, and the values left out of them."""
    seen: dict[str, dict[str, str]] = {}
    for item in annotation["Fields"]:
        source = item["value"]["label"]
        if source in SOURCE_LABELS:
            seen.setdefault(source, {})[item["value"]["tag"]] = item["value"]["tag"]
    row = {"id": doc["id"], "file": f"inv-cdip/docs/{doc['id']}.pdf", "sha256": doc["sha256"]}
    printed, dropped = {}, []
    total_from = "total_amount" if "total_amount" in seen else "amount_due"
    for source, tags in seen.items():
        field = SOURCE_LABELS[source]
        if field == "total" and source != total_from:
            continue
        values = {read_value(field, tag) for tag in tags}
        if len(values) > 1 or None in values:
            reason = "annotated twice, different values" if len(tags) > 1 else "does not read cleanly"
            dropped.append({"id": doc["id"], "field": field, "printed": sorted(tags), "reason": reason})
            continue
        row[field] = values.pop()
        printed[field] = next(iter(tags))
    row["labeled"] = [name for name in LABELED if name in row]
    if "total" in row:
        row["total_from"] = total_from
    row["printed"] = printed
    row["exception"] = None
    return row, dropped


def labels_sha256(path: Path = LABELS) -> str:
    """The hash FROZEN_SHA256 pins: the file's bytes with CRLF read as LF."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def check_frozen() -> None:
    if labels_sha256() != FROZEN_SHA256:
        raise SystemExit(f"{LABELS.name} does not match the set frozen on {FROZEN}; "
                         "a score on it is not comparable to the README's")
    print(f"{LABELS.name} matches the set frozen on {FROZEN}.")


def write_labels(manifest: dict) -> None:
    if LABELS.exists():
        print(f"{LABELS.name} exists and is frozen. Not rewritten.")
        check_frozen()
        return
    rows, dropped, excluded, unlabeled = [], [], [], []
    for doc in manifest["documents"]:
        if doc["pages"] != 1:
            excluded.append({"id": doc["id"], "pages": doc["pages"]})
            continue
        annotation = json.loads((OUT / "annotation" / f"{doc['id']}.json").read_text(encoding="utf-8"))
        row, lost = label_row(doc, annotation)
        dropped += lost
        if row["labeled"]:
            rows.append(row)
        else:
            unlabeled.append(doc["id"])
    out = {
        "set": "inv-cdip",
        "frozen": FROZEN,
        "generator": "get_inv_cdip.py",
        "source": manifest["source"],
        "commit": manifest["commit"],
        "pdf_root": manifest["pdf_root"],
        "attribution": ATTRIBUTION,
        "fields": list(LABELED),
        "exception_labeled": False,
        "excluded": excluded,
        "no_usable_labels": unlabeled,
        "dropped": dropped,
        "invoices": rows,
    }
    LABELS.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    counts = {name: sum(name in row["labeled"] for row in rows) for name in LABELED}
    print(f"labels {len(rows)} invoices, excluded {len(excluded)} multi-page, "
          f"no usable labels {len(unlabeled)}, dropped values {len(dropped)}, per field {counts}")
    check_frozen()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    ids_raw = fetch(OUT / "test_set.txt", f"{REPO}/test_set.txt")
    if ids_raw is None:
        sys.exit("could not fetch test_set.txt")
    ids = [line.strip() for line in ids_raw.decode().splitlines() if line.strip()]
    fetch(OUT / "LICENSE.txt", f"{REPO}/LICENSE.txt")

    docs = []
    for n, doc_id in enumerate(ids, 1):
        anno = fetch(OUT / "annotation" / f"{doc_id}.json", f"{REPO}/annotation/{doc_id}.json")
        pdf = fetch(OUT / "docs" / f"{doc_id}.pdf", pdf_url(doc_id))
        docs.append({
            "id": doc_id,
            "annotation": anno is not None,
            "pdf": pdf is not None,
            "sha256": hashlib.sha256(pdf).hexdigest() if pdf else None,
            "bytes": len(pdf) if pdf else None,
            "pages": pages(pdf) if pdf else None,
        })
        if n % 25 == 0:
            print(f"{n}/{len(ids)}")

    manifest = {
        "source": "https://github.com/salesforce/inv-cdip",
        "commit": COMMIT,
        "pdf_root": PDF_ROOT,
        "license": "annotations CC BY-NC 4.0, copyright 2022 salesforce.com, inc.",
        "documents": docs,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    have = [d for d in docs if d["pdf"] and d["annotation"]]
    multi = [d for d in have if (d["pages"] or 0) > 1]
    print(f"ids {len(ids)}, annotations {sum(d['annotation'] for d in docs)}, "
          f"pdfs {sum(d['pdf'] for d in docs)}, unreadable {sum(1 for d in have if d['pages'] is None)}, "
          f"multi-page {len(multi)}, MB {sum(d['bytes'] or 0 for d in docs) / 1e6:.1f}")
    if len(have) != len(ids):
        sys.exit("some documents are missing; labels not written")
    write_labels(manifest)


if __name__ == "__main__":
    main()
