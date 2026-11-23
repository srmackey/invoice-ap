"""Relabel holdout-2 under the wider payment terms rule.

holdout-2 was labeled under the narrow terms rule ("Net N", "due on receipt",
"2/10 Net 30"). The reader-misses spec (2026-11-21) widened the rule in
../DESIGN.md, so this script recomputes each label's due date and exception
reasons from the terms already in the label. A printed due date is never
touched. The PDFs are not opened.

The rule here is an independent copy, written from DESIGN.md and
holdout-3/BRIEF.md, not from the reader. If the labels came from the reader's
own rule, a mistake in that rule would become the answer key and score as
right. test_holdout2.py imports this copy to recheck the labels.

The holdout is frozen and unread, so the script prints counts and invoice ids
only, never a value. Run it once from the checkout:
  python sandbox/relabel_holdout2.py
A second run changes nothing.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LABELS = ROOT / "holdout-2" / "labels.json"
FIELDS = ("supplier", "invoice_number", "invoice_date", "due_date", "subtotal", "tax", "total")
CENT = Decimal("0.01")
RELABELED = "2026-11-22"
TERMS_RULE = "wider (reader-misses spec, locked 2026-11-21)"

DAY = r"(?:days?|d|tage?n?|jours?|j|dagen|dag|giorni|gg|d[ií]as?)"
NET = r"(?:net|netto|neto)"

# End-of-month and following-month terms, in the six languages.
MONTH_END = re.compile(
    r"\be\.?\s?o\.?\s?m\b|end of (?:the )?month|\bprox(?:imo)?\b|following month|next month"
    r"|month following|of (?:the )?month|fin de(?:l)? mes|fin de mois|mois suivant|monatsende"
    r"|ende des monats|folgemonats?|einde (?:van de )?maand|volgende maand|fine mese|mese successivo"
    r"|mes siguiente"
)

# Periods counted from something other than the invoice date.
OTHER_START = re.compile(
    r"deliver|shipment|\bship(?:ping|ped)?\b|\bgoods\b|merchandise|statement|\bc\.?o\.?d\b"
    r"|lieferung|erhalt|\bware\b|livraison|marchandise|relev[ée]|levering|goederen|afschrift"
    r"|consegna|\bmerce\b|estratto|spedizione|entrega|mercanc[ií]a|extracto|env[ií]o"
)

# Payment on receipt of the invoice, or at once.
RECEIPT = re.compile(
    r"\breceipt\b|\bimmediate(?:ly)?\b|\bsofort\b|r[ée]ception|ontvangst|ricevimento|ricezione"
    r"|recepci[oó]n|\bper direct\b"
)

DATE = re.compile(r"\b\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}\b")
NET_SLASH = re.compile(rf"\b(?:{NET}|n)\.?\s*/?\s*(\d+)\b")
PERCENT_DISCOUNT = re.compile(
    rf"\d+(?:[.,]\d+)?\s*%\s*(?:skonto|escompte|sconto|descuento|korting|discount|disc\.?|rabatt)?"
    rf"\s*,?\s*(?:if paid\s*)?(?:bei zahlung\s*)?"
    rf"(?:(?:within|innerhalb(?: von)?|dentro de|entro|binnen|bis|in|à|a|en)\b)?\s*(?:(\d+)\s*{DAY}?\b)?"
)
SLASH_DISCOUNT = re.compile(r"\b\d{1,2}(?:[.,]\d+)?\s*/\s*\d{1,3}\b")
MONEY = re.compile(r"[$€£]?\s*\d+(?:[.,\s]\d{3})*[.,]\d+")
NUMBER = re.compile(r"\b\d+\b")


def normal(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"\s+", " ", text.replace(" ", " ").replace("\xa0", " ")).strip()


def terms_days(terms: str | None) -> int | None:
    """Days to the due date under the wider rule, or None where the due date stays null."""
    if not terms:
        return None
    text = normal(terms)
    if MONTH_END.search(text) or OTHER_START.search(text):
        return None
    text = DATE.sub(" ", text)
    text = NET_SLASH.sub(lambda m: f"net {m.group(1)}", text)
    # A discount counts only with its own period beside it, so a late fee
    # ("1.5% per month") is not taken for discount terms.
    discount = []
    text = PERCENT_DISCOUNT.sub(lambda m: discount.append(m.group(1)) or " ", text)
    discount = any(discount)
    text, found = SLASH_DISCOUNT.subn(" ", text)
    discount = discount or bool(found)
    text = MONEY.sub(" ", text)
    periods = {int(n) for n in NUMBER.findall(text)}
    if not periods:
        if discount:
            return None
        return 0 if RECEIPT.search(text) else None
    if len(periods) > 1 or RECEIPT.search(text):
        return None
    if not (re.search(rf"\b{NET}\b", text) or re.search(rf"\d\s*{DAY}\b", text)):
        return None
    days = periods.pop()
    return days if 1 <= days <= 180 else None


def rule(row: dict) -> list[str]:
    """The exception rule in DESIGN.md, as it applies to a dollar-only set."""
    reasons = [f"missing_{name}" for name in FIELDS if row[name] in (None, "")]
    if row["invoice_date"] and row["due_date"] and row["due_date"] < row["invoice_date"]:
        reasons.append("due_before_invoice")
    if all(row[name] not in (None, "") for name in ("subtotal", "tax", "total")):
        if abs(Decimal(row["subtotal"]) + Decimal(row["tax"]) - Decimal(row["total"])) > CENT:
            reasons.append("total_mismatch")
    return reasons


def due(row: dict) -> tuple[str | None, str | None]:
    """The label's due date and its source under the wider rule."""
    if row["due_source"] == "printed":
        return row["due_date"], "printed"
    days = terms_days(row.get("terms"))
    if days is None or not row["invoice_date"]:
        return None, None
    return (date.fromisoformat(row["invoice_date"]) + timedelta(days=days)).isoformat(), "terms"


def same_order(old: list[str], new: list[str]) -> list[str]:
    """New reasons, keeping the delivered order for any that were already there."""
    kept = [r for r in old if r in new]
    return kept + [r for r in new if r not in kept]


def dump(payload: dict, style: dict) -> str:
    return json.dumps(payload, **style["args"]) + style["end"]


def find_style(raw: str, payload: dict) -> dict | None:
    """The json.dumps settings that reproduce the file byte for byte, so the diff is only the relabel."""
    for indent in (2, 4, 1, None):
        for ascii_only in (False, True):
            for end in ("\n", ""):
                style = {"args": {"indent": indent, "ensure_ascii": ascii_only}, "end": end}
                if dump(payload, style) == raw:
                    return style
    return None


def main() -> int:
    data = LABELS.read_bytes()
    newline = "\r\n" if b"\r\n" in data else "\n"
    raw = data.decode("utf-8").replace("\r\n", "\n")
    payload = json.loads(raw)
    style = find_style(raw, payload)
    if style is None:
        print("labels.json formatting could not be reproduced; nothing written")
        return 1

    counts = {"printed": 0, "no terms": 0, "terms left null": 0, "gained": 0, "lost": 0, "moved": 0,
              "unchanged": 0, "now exception": 0, "now clean": 0}
    changed = []
    for row in payload["invoices"]:
        if row["due_source"] == "printed":
            counts["printed"] += 1
            continue
        if not row.get("terms"):
            counts["no terms"] += 1
        elif terms_days(row["terms"]) is None:
            counts["terms left null"] += 1
        old_due, old_reasons, old_exception = row["due_date"], row["exception_reasons"], row["exception"]
        row["due_date"], row["due_source"] = due(row)
        reasons = same_order(old_reasons, rule(row))
        if sorted(reasons) != sorted(old_reasons):
            row["exception_reasons"] = reasons
        row["exception"] = bool(row["exception_reasons"])
        if row["due_date"] == old_due:
            counts["unchanged"] += 1
            continue
        changed.append(row["id"])
        counts["gained" if old_due is None else "lost" if row["due_date"] is None else "moved"] += 1
        if row["exception"] != old_exception:
            counts["now exception" if row["exception"] else "now clean"] += 1

    if payload.get("relabeled") != RELABELED or payload.get("terms_rule") != TERMS_RULE:
        rebuilt = {}
        for key, value in payload.items():
            if key in ("relabeled", "terms_rule"):
                continue
            rebuilt[key] = value
            if key == "frozen":
                rebuilt["relabeled"] = RELABELED
                rebuilt["terms_rule"] = TERMS_RULE
        payload = rebuilt

    out = dump(payload, style)
    total = len(payload["invoices"])
    terms_set = sum(1 for row in payload["invoices"] if row["due_source"] == "terms")
    exceptions = sum(1 for row in payload["invoices"] if row["exception"])
    print(f"invoices {total}; printed due dates {counts['printed']} (untouched)")
    print(f"not printed: no terms {counts['no terms']}, terms the rule leaves null {counts['terms left null']}")
    print(f"due dates: gained {counts['gained']}, lost {counts['lost']}, moved {counts['moved']}, "
          f"unchanged {counts['unchanged']}")
    print(f"exceptions: now raised {counts['now exception']}, now clean {counts['now clean']}")
    print(f"after: {terms_set} due dates from terms, {exceptions} exceptions")
    print("changed ids:", " ".join(changed) if changed else "none")
    if out == raw:
        print("labels.json already matches; nothing written")
        return 0
    with LABELS.open("w", encoding="utf-8", newline=newline) as handle:
        handle.write(out)
    print("labels.json written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
