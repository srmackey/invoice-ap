"""Write the invoice-ap sandbox PDFs and labels.json.

The field list and the exception rule live in ../AGENTS.md. This script
renders that rule. It does not score a run. Needs reportlab.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
LABELS = ROOT / "labels.json"
FIELDS = (
    "supplier",
    "invoice_number",
    "invoice_date",
    "due_date",
    "subtotal",
    "tax",
    "total",
)
BUYER = ("Larkspur Market", "Attn: Accounts payable", "220 Market Street", "Dayton, OH 45402")
CENT = Decimal("0.01")


def money(value: str) -> Decimal:
    return Decimal(value).quantize(CENT)


def money_pdf(value: Decimal) -> str:
    return f"${value:,.2f}"


def date_pdf(iso: str) -> str:
    day = datetime.strptime(iso, "%Y-%m-%d")
    return f"{day.strftime('%B')} {day.day}, {day.year}"


def line_amount(qty: str, unit: str) -> Decimal:
    return (Decimal(qty) * Decimal(unit)).quantize(CENT)


# Lines sum to the printed subtotal. The bad total is a printed total only.
INVOICES = [
    {
        "id": "01-northline",
        "supplier": "Northline Supply Co.",
        "address": ("418 Wharf Street", "Duluth, MN 55802"),
        "invoice_number": "NL-10442",
        "invoice_date": "2026-02-03",
        "due_date": "2026-03-05",
        "lines": (
            ("Packing tape, 2 in", "40", "18.50"),
            ("Stretch wrap, 80 gauge", "20", "26.00"),
        ),
        "tax": "100.80",
        "total": "1360.80",
        "expect": [],
    },
    {
        "id": "02-harbor-paper",
        "supplier": "Harbor Paper Ltd.",
        "address": ("90 Pier Lane", "Portland, ME 04101"),
        "invoice_number": "HP-8831",
        "invoice_date": "2026-01-15",
        "due_date": "2026-02-14",
        "lines": (
            ("Copy paper, case", "10", "42.00"),
            ("No. 10 envelopes, box", "5", "16.40"),
        ),
        "tax": "40.16",
        "total": "542.16",
        "expect": [],
    },
    {
        "id": "03-keel-electric",
        "supplier": "Keel Electric",
        "address": ("15 Yardarm Road", "New Bedford, MA 02740"),
        "invoice_number": "KE-2207",
        "invoice_date": "2026-04-02",
        "due_date": "2026-04-17",
        "lines": (
            ("Service call", "1", "185.00"),
            ("Breaker, 20 amp", "2", "24.50"),
        ),
        "tax": "0.00",
        "total": "234.00",
        "expect": [],
    },
    {
        "id": "04-brightwater",
        "supplier": "Brightwater Janitorial",
        "address": ("640 Wash Avenue", "Toledo, OH 43604"),
        "invoice_number": "BW-551",
        "invoice_date": "2026-03-01",
        "due_date": "2026-03-31",
        "lines": (("Monthly cleaning", "1", "890.00"),),
        "tax": "71.20",
        "total": "961.20",
        "expect": [],
    },
    {
        "id": "05-cedar-rye",
        "supplier": "Cedar & Rye Packaging",
        "address": ("88 Mill Road", "Bend, OR 97701"),
        "invoice_number": "CR-77821",
        "invoice_date": "2026-05-19",
        "due_date": "2026-06-18",
        "lines": (
            ("Mailer box, 12 in", "200", "1.25"),
            ("Void fill, bag", "15", "12.00"),
            ("Packing tape", "30", "4.50"),
        ),
        "tax": "45.20",
        "total": "610.20",
        "expect": [],
    },
    {
        "id": "06-motspur",
        "supplier": "Motspur Tools",
        "address": ("7 Forge Street", "Akron, OH 44308"),
        "invoice_number": "MT-3190",
        "invoice_date": "2026-06-08",
        "due_date": "2026-07-08",
        "lines": (
            ("Drill bit set", "3", "64.00"),
            ("Spring clamp", "12", "9.75"),
        ),
        "tax": "24.72",
        "total": "333.72",
        "expect": [],
    },
    {
        "id": "07-fieldstone",
        "supplier": "Fieldstone Fuel",
        "address": ("1200 County Road 4", "Lima, OH 45801"),
        "invoice_number": "FF-10094",
        "invoice_date": "2026-02-27",
        "due_date": "2026-03-13",
        "lines": (("Diesel, gallon", "400", "3.45"),),
        "tax": "110.40",
        "total": "1490.40",
        "expect": [],
    },
    {
        "id": "08-westwind",
        "supplier": "Westwind Chemicals",
        "address": ("50 Tank Farm Road", "Gary, IN 46402"),
        "invoice_number": "WW-642",
        "invoice_date": "2026-07-09",
        "due_date": None,
        "lines": (("Solvent, drum", "6", "75.00"),),
        "tax": "36.00",
        "total": "486.00",
        "expect": ["missing_due_date"],
    },
    {
        "id": "09-oak-street",
        "supplier": "Oak Street Lumber",
        "address": ("310 Oak Street", "Madison, WI 53703"),
        "invoice_number": "OSL-2088",
        "invoice_date": "2026-03-12",
        "due_date": "2026-03-01",
        "lines": (
            ("Stud, 2x4x8", "80", "6.25"),
            ("Plywood, 4x8", "10", "32.00"),
        ),
        "tax": "65.60",
        "total": "885.60",
        "expect": ["due_before_invoice"],
    },
    {
        "id": "10-pinion",
        "supplier": "Pinion Parts",
        "address": ("22 Gear Avenue", "Fort Wayne, IN 46802"),
        "invoice_number": "PP-4471",
        "invoice_date": "2026-08-21",
        "due_date": "2026-09-20",
        "lines": (
            ("Bearing", "8", "40.00"),
            ("Seal", "16", "10.00"),
        ),
        "tax": "38.40",
        "total": "500.00",
        "expect": ["total_mismatch"],
    },
]


def subtotal_of(invoice: dict) -> Decimal:
    return sum(
        (line_amount(qty, unit) for _, qty, unit in invoice["lines"]),
        Decimal("0.00"),
    ).quantize(CENT)


def reasons_for(invoice: dict, subtotal: Decimal) -> list[str]:
    reasons = []
    values = {
        "supplier": invoice["supplier"],
        "invoice_number": invoice["invoice_number"],
        "invoice_date": invoice["invoice_date"],
        "due_date": invoice["due_date"],
        "subtotal": subtotal,
        "tax": money(invoice["tax"]),
        "total": money(invoice["total"]),
    }
    for field in FIELDS:
        if values[field] is None or values[field] == "":
            reasons.append(f"missing_{field}")
    if invoice["invoice_date"] and invoice["due_date"]:
        if invoice["due_date"] < invoice["invoice_date"]:
            reasons.append("due_before_invoice")
    if abs(subtotal + money(invoice["tax"]) - money(invoice["total"])) > CENT:
        reasons.append("total_mismatch")
    return reasons


def styles():
    base = ParagraphStyle("base", fontName="Helvetica", fontSize=10, leading=13, textColor=colors.HexColor("#1c1c1c"))
    return {
        "base": base,
        "supplier": ParagraphStyle("supplier", parent=base, fontName="Helvetica-Bold", fontSize=16, leading=19, textColor=colors.HexColor("#1f3a5f")),
        "small": ParagraphStyle("small", parent=base, fontSize=9, leading=12, textColor=colors.HexColor("#333333")),
        "label": ParagraphStyle("label", parent=base, fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=colors.HexColor("#1f3a5f")),
        "right_label": ParagraphStyle("right_label", parent=base, fontName="Helvetica-Bold", fontSize=9, leading=12, alignment=2, textColor=colors.HexColor("#1f3a5f")),
        "invoice": ParagraphStyle("invoice", parent=base, fontName="Helvetica-Bold", fontSize=14, leading=17, alignment=2, textColor=colors.HexColor("#1f3a5f")),
        "head": ParagraphStyle("head", parent=base, fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=colors.white),
        "money": ParagraphStyle("money", parent=base, alignment=2),
        "total": ParagraphStyle("total", parent=base, fontName="Helvetica-Bold", alignment=2),
        "total_label": ParagraphStyle("total_label", parent=base, fontName="Helvetica-Bold", alignment=2),
    }


def cell(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(text, style)


def write_pdf(invoice: dict, subtotal: Decimal, path: Path) -> None:
    look = styles()
    story = []
    left_flow = [
        Paragraph(invoice["supplier"], look["supplier"]),
        Spacer(1, 4),
        Paragraph("<br/>".join(invoice["address"]), look["small"]),
    ]
    header = Table(
        [[left_flow, Paragraph("INVOICE", look["invoice"])]],
        colWidths=[4.6 * inch, 2.5 * inch],
    )
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 0), (-1, -1), 1, colors.HexColor("#1f3a5f")),
    ]))
    story.append(header)
    story.append(Spacer(1, 14))

    meta = [[cell("Invoice number", look["right_label"]), cell(invoice["invoice_number"], look["base"])],
            [cell("Invoice date", look["right_label"]), cell(date_pdf(invoice["invoice_date"]), look["base"])]]
    if invoice["due_date"]:
        meta.append([cell("Due date", look["right_label"]), cell(date_pdf(invoice["due_date"]), look["base"])])
    meta_table = Table(meta, colWidths=[1.4 * inch, 1.8 * inch], hAlign="RIGHT")
    meta_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f4f7fb")),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 16))
    story.append(Paragraph("Bill to", look["label"]))
    story.append(Spacer(1, 2))
    story.append(Paragraph("<br/>".join(BUYER), look["base"]))
    story.append(Spacer(1, 16))

    head = [cell(name, look["head"]) for name in ("Description", "Qty", "Unit price", "Amount")]
    rows = [head]
    for desc, qty, unit in invoice["lines"]:
        amount = line_amount(qty, unit)
        rows.append([
            cell(desc, look["base"]),
            cell(qty, look["money"]),
            cell(money_pdf(money(unit)), look["money"]),
            cell(money_pdf(amount), look["money"]),
        ])
    grid = Table(rows, colWidths=[3.7 * inch, 0.8 * inch, 1.3 * inch, 1.3 * inch])
    grid.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c5ced8")),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f9fb")]),
    ]))
    story.append(grid)
    story.append(Spacer(1, 12))

    totals = [
        [cell("Subtotal", look["right_label"]), cell(money_pdf(subtotal), look["money"])],
        [cell("Tax", look["right_label"]), cell(money_pdf(money(invoice["tax"])), look["money"])],
        [cell("Total", look["total_label"]), cell(money_pdf(money(invoice["total"])), look["total"])],
    ]
    totals_table = Table(totals, colWidths=[1.3 * inch, 1.3 * inch], hAlign="RIGHT")
    totals_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEABOVE", (0, -1), (-1, -1), 1, colors.HexColor("#1f3a5f")),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f4f7fb")),
    ]))
    story.append(totals_table)
    story.append(Spacer(1, 18))
    story.append(Paragraph("Amounts are USD.", look["small"]))

    path.parent.mkdir(parents=True, exist_ok=True)

    def stamp(canvas, _doc):
        canvas.setTitle(f"{invoice['supplier']} {invoice['invoice_number']}")
        canvas.setAuthor(invoice["supplier"])

    SimpleDocTemplate(
        str(path),
        pagesize=letter,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.7 * inch,
        title=f"{invoice['supplier']} {invoice['invoice_number']}",
        author=invoice["supplier"],
    ).build(story, onFirstPage=stamp)


def label_for(invoice: dict, subtotal: Decimal, found: list[str]) -> dict:
    return {
        "id": invoice["id"],
        "file": f"fixtures/{invoice['id']}.pdf",
        "supplier": invoice["supplier"],
        "invoice_number": invoice["invoice_number"],
        "invoice_date": invoice["invoice_date"],
        "due_date": invoice["due_date"],
        "subtotal": f"{subtotal:.2f}",
        "tax": f"{money(invoice['tax']):.2f}",
        "total": f"{money(invoice['total']):.2f}",
        "exception": bool(found),
        "exception_reasons": found,
    }


def main() -> None:
    labels = []
    for invoice in INVOICES:
        subtotal = subtotal_of(invoice)
        found = reasons_for(invoice, subtotal)
        if found != invoice["expect"]:
            raise SystemExit(f"{invoice['id']}: reasons {found} != {invoice['expect']}")
        write_pdf(invoice, subtotal, FIXTURES / f"{invoice['id']}.pdf")
        labels.append(label_for(invoice, subtotal, found))
    payload = {"fields": list(FIELDS), "invoices": labels}
    LABELS.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    exceptions = sum(1 for row in labels if row["exception"])
    print(f"wrote {len(labels)} invoices, {exceptions} need an exception")


if __name__ == "__main__":
    main()
