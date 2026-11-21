"""Write the terms practice set: sandbox/practice/ and its labels.json.

Ten one-page invoices for developing the payment-terms rule and the
amount-due fallback in DESIGN.md. They print terms in the places a supplier
puts them (a header grid, a line of their own, the due-date slot) and some
print an amount due where the total would go. This set is practice, not a
benchmark: it is not claimed, it may be regenerated, and nothing here is
drawn from the holdout. Needs reportlab.

Run from the checkout:
  python sandbox/make_practice.py
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "practice"
FIELDS = ("supplier", "invoice_number", "invoice_date", "due_date", "subtotal", "tax", "total")
CENT = Decimal("0.01")
BUYER = ("Bill to:", "Copperleaf Bakery", "71 Orchard Lane", "Bend, OR 97701")

# terms_days is the label's own copy of the rule, so the labels do not lean on
# the reader they will judge.
INVOICES = [
    {"id": "p01-tidewater", "supplier": "Tidewater Packaging", "number": "TW-5012", "date": date(2026, 3, 2),
     "terms": "Net 30", "terms_at": "grid", "lines": [("Kraft boxes, case", 12, "18.50"), ("Tape, roll", 24, "3.25")],
     "tax_rate": "0.07", "days": 30},
    {"id": "p02-granite", "supplier": "Granite Ridge Equipment", "number": "GR-0091", "date": date(2026, 1, 20),
     "terms": "2/10 Net 30", "terms_at": "line", "terms_label": "Payment Terms:", "lines": [("Mixer service", 1, "240.00")],
     "tax_rate": "0.00", "days": 30},
    {"id": "p03-bluefern", "supplier": "Bluefern Linens", "number": "BF-7734", "date": date(2026, 2, 11),
     "terms": "Due on receipt", "terms_at": "alone", "lines": [("Apron rental, week", 8, "4.75"), ("Towels, bundle", 3, "11.00")],
     "tax_rate": "0.05", "days": 0},
    {"id": "p04-hollis", "supplier": "Hollis Dairy Cooperative", "number": "HDC-2290", "date": date(2026, 3, 9),
     "terms": "Upon receipt", "terms_at": "due_slot", "lines": [("Butter, 36 lb case", 2, "112.40")],
     "tax_rate": "0.00", "days": 0},
    {"id": "p05-arden", "supplier": "Arden Flour Mills", "number": "AFM-3318", "date": date(2026, 2, 26),
     "terms": "Net 30 EOM", "terms_at": "grid", "lines": [("Bread flour, 50 lb", 10, "27.80")],
     "tax_rate": "0.00", "days": None},
    {"id": "p06-cobalt", "supplier": "Cobalt Refrigeration", "number": "CR-4471", "date": date(2026, 1, 5),
     "terms": "Net 45", "terms_at": "grid", "due": date(2026, 1, 31), "lines": [("Compressor repair", 1, "615.00")],
     "tax_rate": "0.06", "days": 45},
    {"id": "p07-marlowe", "supplier": "Marlowe Produce", "number": "MP-1180", "date": date(2026, 3, 16),
     "terms": "Net 15", "terms_at": "line", "terms_label": "Terms:", "total_label": "Amount Due",
     "lines": [("Apples, bushel", 4, "38.00"), ("Lemons, case", 2, "29.50")], "tax_rate": "0.00", "days": 15},
    {"id": "p08-kestrel", "supplier": "Kestrel Print Shop", "number": "KP-0412", "date": date(2026, 2, 2),
     "terms": None, "due": date(2026, 2, 16), "total_label": "Balance Due",
     "lines": [("Menu cards, 500", 1, "185.00"), ("Window decal", 2, "42.00")], "tax_rate": "0.08", "days": None},
    {"id": "p09-sunder", "supplier": "Sunder Pest Control", "number": "SPC-9921", "date": date(2026, 3, 1),
     "terms": "NET 60 DAYS", "terms_at": "grid", "lines": [("Quarterly service", 1, "160.00")],
     "tax_rate": "0.0725", "days": 60},
    {"id": "p10-wexford", "supplier": "Wexford Ovens", "number": "WO-2051", "date": date(2026, 2, 18),
     "terms": "Net 30", "terms_at": "grid", "total_label": "Amount Due", "deposit": "500.00",
     "lines": [("Deck oven install", 1, "1800.00")], "tax_rate": "0.00", "days": 30},
]


def money(value) -> Decimal:
    return Decimal(value).quantize(CENT)


def shown(value: Decimal) -> str:
    return f"${value:,.2f}"


def date_text(day: date) -> str:
    return day.strftime("%m/%d/%Y")


def amounts(inv: dict) -> tuple[Decimal, Decimal, Decimal, Decimal | None]:
    subtotal = sum((money(Decimal(qty) * Decimal(unit)) for _d, qty, unit in inv["lines"]), Decimal("0.00"))
    tax = money(subtotal * Decimal(inv["tax_rate"]))
    total = subtotal + tax
    due = total - money(inv["deposit"]) if inv.get("deposit") else None
    return subtotal, tax, total, due


def label(inv: dict) -> dict:
    subtotal, tax, total, after_deposit = amounts(inv)
    if inv.get("due"):
        due = inv["due"]
    elif inv["days"] is not None:
        due = inv["date"] + timedelta(days=inv["days"])
    else:
        due = None
    printed_total = after_deposit if after_deposit is not None else total
    row = {
        "id": inv["id"],
        "file": f"practice/{inv['id']}.pdf",
        "supplier": inv["supplier"],
        "invoice_number": inv["number"],
        "invoice_date": inv["date"].isoformat(),
        "due_date": due.isoformat() if due else None,
        "subtotal": f"{subtotal:.2f}",
        "tax": f"{tax:.2f}",
        "total": f"{printed_total:.2f}",
        "due_from": "printed" if inv.get("due") else ("terms" if due else None),
    }
    reasons = []
    for name in FIELDS:
        if row[name] is None:
            reasons.append(f"missing_{name}")
    if abs(subtotal + tax - printed_total) > CENT:
        reasons.append("total_mismatch")
    row["exception"] = bool(reasons)
    row["exception_reasons"] = reasons
    return row


def write_pdf(inv: dict, path: Path) -> None:
    body = ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=13)
    head = ParagraphStyle("head", fontName="Helvetica-Bold", fontSize=18, leading=22)
    subtotal, tax, total, after_deposit = amounts(inv)
    story = [Paragraph(inv["supplier"], head), Spacer(1, 0.15 * inch)]
    grid = [["Invoice #", inv["number"]], ["Invoice Date", date_text(inv["date"])]]
    if inv.get("due"):
        grid.append(["Due Date", date_text(inv["due"])])
    if inv.get("terms_at") == "due_slot":
        grid.append(["Due Date", inv["terms"]])
    if inv.get("terms_at") == "grid":
        grid.append(["Terms", inv["terms"]])
    header = Table(grid, colWidths=[1.3 * inch, 1.8 * inch])
    header.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey), ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold")]))
    buyer = Paragraph("<br/>".join(BUYER), body)
    story += [Table([[buyer, header]], colWidths=[3.4 * inch, 3.3 * inch]), Spacer(1, 0.25 * inch)]
    if inv.get("terms_at") == "line":
        story += [Paragraph(f"{inv['terms_label']} {inv['terms']}", body), Spacer(1, 0.15 * inch)]
    rows = [["Description", "Qty", "Unit", "Amount"]]
    for desc, qty, unit in inv["lines"]:
        rows.append([desc, str(qty), shown(money(unit)), shown(money(Decimal(qty) * Decimal(unit)))])
    items = Table(rows, colWidths=[3.6 * inch, 0.7 * inch, 1.1 * inch, 1.3 * inch])
    items.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black), ("ALIGN", (1, 0), (-1, -1), "RIGHT")]))
    story += [items, Spacer(1, 0.2 * inch)]
    totals = [["Subtotal", shown(subtotal)], ["Tax", shown(tax)]]
    if inv.get("deposit"):
        totals.append(["Less deposit", f"-{shown(money(inv['deposit']))}"])
        totals.append([inv["total_label"], shown(after_deposit)])
    else:
        totals.append([inv.get("total_label", "Total"), shown(total)])
    box = Table(totals, colWidths=[1.4 * inch, 1.3 * inch], hAlign="RIGHT")
    box.setStyle(TableStyle([("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                             ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black)]))
    story.append(box)
    if inv.get("terms_at") == "alone":
        story += [Spacer(1, 0.3 * inch), Paragraph(inv["terms"], body)]
    story += [Spacer(1, 0.4 * inch), Paragraph("Thank you for your business.", body)]
    SimpleDocTemplate(str(path), pagesize=letter, title=inv["id"]).build(story)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    rows = []
    for inv in INVOICES:
        write_pdf(inv, OUT / f"{inv['id']}.pdf")
        rows.append(label(inv))
    labels = {
        "fields": list(FIELDS),
        "note": "Practice set for the terms rule and the amount-due fallback. Not a benchmark and not claimed.",
        "invoices": rows,
    }
    (OUT / "labels.json").write_text(json.dumps(labels, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} invoices to {OUT}")


if __name__ == "__main__":
    main()
