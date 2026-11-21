"""Write the generated holdout under holdout/. Run it once.

One hundred one-page invoices in twelve layouts that make_fixtures.py and
make_hard.py do not produce. Varied label words, payment terms with and
without a printed due date, PO numbers and ship dates beside the invoice
fields, two-column headers, boxed totals, a landscape page, and day-first
dates where the page makes them unambiguous (a written month, or a numeric
day above 12). Half are clean PDFs; a quarter are scans and a quarter are
phone photos, rendered the way make_hard.py renders them.

About a quarter need an exception: a missing due date (with or without
printed terms), a missing invoice number, a due date before the invoice
date, or a printed total that does not equal subtotal plus tax.

A due date implied by terms ("Net 30") but not printed is missing on the
page, so its label is null and the rule raises missing_due_date. That is
the reading of the null rule in ../AGENTS.md.

The set is a holdout: frozen before any runner reads it, scored once per
runner, never used to change a mapper. This script was written without
reference to any runner's output, and it refuses to run when
holdout/labels.json already exists. Labels carry each file's sha256 so
test_holdout.py can tell the set has not changed.

Needs sandbox/.venv (reportlab, pymupdf, pillow, numpy).
"""

from __future__ import annotations

import hashlib
import json
import random
import tempfile
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from xml.sax.saxutils import escape

import pymupdf
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from make_fixtures import BUYER, FIELDS
from make_hard import render_photo, render_scan

ROOT = Path(__file__).resolve().parent
HOLDOUT = ROOT / "holdout"
LABELS = HOLDOUT / "labels.json"
SEED = 20261121
FROZEN = "2026-11-21"
CENT = Decimal("0.01")
FOLDERS = {"clean": "clean", "scan": "scans", "photo": "photos"}

KINDS = (
    ["clean"] * 75
    + ["no_due_terms"] * 4
    + ["no_due"] * 2
    + ["no_number"] * 3
    + ["due_before"] * 8
    + ["mismatch"] * 8
)
RENDERS = ["clean"] * 50 + ["scan"] * 25 + ["photo"] * 25
EXPECT = {
    "clean": [],
    "no_due_terms": ["missing_due_date"],
    "no_due": ["missing_due_date"],
    "no_number": ["missing_invoice_number"],
    "due_before": ["due_before_invoice"],
    "mismatch": ["total_mismatch"],
}
MISMATCH = [
    Decimal("10.00"), Decimal("-25.00"), Decimal("0.90"), Decimal("100.00"),
    Decimal("-1.00"), Decimal("0.50"), Decimal("9.00"), Decimal("-0.20"),
]

FIRST = [
    "Alder", "Basalt", "Copperline", "Driftwood", "Ember", "Foxglove", "Granite", "Heron",
    "Ironbark", "Juniper", "Kestrel", "Linden", "Millrace", "Nettle", "Orchard", "Pewter",
    "Quillon", "Redfern", "Saltmarsh", "Tamarack", "Umber", "Verdant", "Willowmere", "Yarrow",
    "Zephyr", "Bramble", "Cobalt", "Dunmore", "Elmhurst", "Flint", "Gable", "Halyard",
    "Inkwell", "Jetty", "Keystone", "Lantern", "Meridian", "Northgate", "Oxbow", "Prairie",
    "Riverbend", "Sumac", "Thistle", "Upland", "Vantage", "Wren", "Brightline", "Calder",
    "Delta", "Evergreen",
]
TRADE = [
    "Supply", "Office Products", "Hardware", "Linen Service", "Electrical", "Plumbing",
    "Signworks", "Coffee Roasters", "Logistics", "Packaging", "Print Co.", "Fasteners",
    "Uniforms", "Pest Control", "Landscaping", "Refrigeration", "Lumber", "Paper Goods",
    "Bakery Supply", "Auto Parts", "Glass", "Chemical", "Waste Services", "IT Services",
    "Catering",
]
SUFFIX = ["", "", " Inc.", " LLC", " Co.", " & Sons", " Ltd.", " Group"]
STREETS = [
    "Harbor Road", "Mill Street", "Quarry Lane", "Depot Avenue", "Canal Street", "Ridge Road",
    "Foundry Way", "Station Street", "Market Square", "Orchard Lane", "Commerce Drive",
    "Industrial Parkway", "Front Street", "Bridge Road", "Elm Street", "Kiln Court",
]
CITIES = [
    ("Columbus", "OH", "43215"), ("Erie", "PA", "16501"), ("Peoria", "IL", "61602"),
    ("Grand Rapids", "MI", "49503"), ("Louisville", "KY", "40202"), ("Des Moines", "IA", "50309"),
    ("Syracuse", "NY", "13202"), ("Knoxville", "TN", "37902"), ("Omaha", "NE", "68102"),
    ("Spokane", "WA", "99201"), ("Tulsa", "OK", "74103"), ("Burlington", "VT", "05401"),
    ("Reno", "NV", "89501"), ("Savannah", "GA", "31401"), ("Boise", "ID", "83702"),
]
ITEMS = [
    ("Service call", 85, 240), ("Labor, hour", 45, 120), ("Copy paper, case", 30, 60),
    ("Toner cartridge", 60, 180), ("Hex bolt, box of 100", 9, 28), ("Shop towels, case", 18, 45),
    ("Floor mat rental", 6, 22), ("Uniform rental, week", 12, 40), ("Coffee beans, 5 lb", 38, 70),
    ("Paper cups, sleeve", 4, 12), ("Corrugated box, bundle", 20, 55), ("Pallet wrap, roll", 14, 30),
    ("LED fixture", 40, 160), ("Wire, 12 AWG, spool", 55, 130), ("PVC fitting", 1, 6),
    ("Monthly service", 150, 900), ("Delivery fee", 15, 75), ("Fuel surcharge", 5, 40),
    ("Signage panel", 80, 420), ("Window cleaning", 90, 300), ("Refrigerant, lb", 18, 60),
    ("Mulch, yard", 28, 48), ("Lumber, 2x6x10", 7, 16), ("Glass pane, cut to size", 30, 140),
    ("Brake pads, set", 35, 95), ("Degreaser, gallon", 16, 38), ("Dumpster pickup", 60, 220),
    ("Network switch", 120, 480), ("Catering tray", 45, 160), ("Flour, 50 lb sack", 22, 40),
]
QTYS = [1, 1, 1, 2, 2, 3, 4, 5, 6, 8, 10, 12, 20, 24, 40]
TERMS = [10, 15, 30, 30, 30, 45, 60]
TAX_RATES = ["0", "0.05", "0.0575", "0.06", "0.07", "0.0825", "0.10"]
START = date(2025, 1, 6)
SPAN = 660

LAYOUTS = [
    "ledger", "split-header", "boxed-totals", "banner", "narrow-statement", "day-first",
    "rules-only", "sidebar", "inline-totals", "letter", "landscape", "summary-first",
]
DAY_FIRST_NUMERIC = {"day-first"}


# ---------- values ----------

def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def rule(values: dict) -> list[str]:
    """The exception rule in ../AGENTS.md, over label values."""
    reasons = [f"missing_{name}" for name in FIELDS if values.get(name) in (None, "")]
    if values.get("invoice_date") and values.get("due_date"):
        if values["due_date"] < values["invoice_date"]:
            reasons.append("due_before_invoice")
    parts = [values.get(name) for name in ("subtotal", "tax", "total")]
    if all(part not in (None, "") for part in parts):
        subtotal, tax, total = (Decimal(part) for part in parts)
        if abs(subtotal + tax - total) > CENT:
            reasons.append("total_mismatch")
    return reasons


def suppliers(rng: random.Random, count: int) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    while len(names) < count:
        first = rng.choice(FIRST)
        trade = rng.choice(TRADE)
        suffix = "" if trade.endswith("Co.") else rng.choice(SUFFIX)
        name = f"{first} {trade}{suffix}"
        if first in {n.split()[0] for n in names} and len(names) < len(FIRST):
            continue
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


def initials(name: str) -> str:
    return "".join(word[0] for word in name.replace("&", "").split() if word[0].isalpha())[:3].upper()


def invoice_number(rng: random.Random, supplier: str, day: date) -> str:
    style = rng.randrange(6)
    if style == 0:
        return f"{initials(supplier)}-{rng.randint(1000, 99999)}"
    if style == 1:
        return f"INV-{day.year}-{rng.randint(1, 9999):04d}"
    if style == 2:
        return f"{rng.randint(10000, 999999):06d}"
    if style == 3:
        return f"{initials(supplier)}{rng.randint(100, 9999)}"
    if style == 4:
        return f"{rng.randint(100, 4999)}/{day.strftime('%y')}"
    return f"{rng.randint(1000, 9999)}-{rng.choice('ABCDEFGH')}"


def pick_day(rng: random.Random, numeric_day_first: bool) -> date:
    while True:
        day = START + timedelta(days=rng.randrange(SPAN))
        if not numeric_day_first or day.day > 12:
            return day


def offset(rng: random.Random, base: date, low: int, high: int, numeric_day_first: bool) -> date:
    """A day between base+low and base+high. On a day-first numeric page the
    day must be above 12; a window too short to hold one grows backward."""
    if numeric_day_first:
        while not any((base + timedelta(days=n)).day > 12 for n in range(low, high + 1)):
            low -= 7
    while True:
        day = base + timedelta(days=rng.randint(low, high))
        if not numeric_day_first or day.day > 12:
            return day


def build_invoice(rng: random.Random, index: int, supplier: str, kind: str, layout: str, render: str,
                  mismatch: Decimal | None) -> dict:
    numeric = layout in DAY_FIRST_NUMERIC
    issued = pick_day(rng, numeric)
    terms = rng.choice(TERMS)
    show_terms = rng.random() < 0.45
    due: date | None
    if kind in ("no_due", "no_due_terms"):
        due = None
        show_terms = kind == "no_due_terms"
    elif kind == "due_before":
        due = offset(rng, issued, -24, -3, numeric)
        show_terms = False
    elif numeric:
        due = offset(rng, issued, terms - 3, terms + 3, True)
        show_terms = False
    else:
        due = issued + timedelta(days=terms)
    number = None if kind == "no_number" else invoice_number(rng, supplier, issued)

    lines = []
    for desc, low, high in rng.sample(ITEMS, rng.randint(1, 4)):
        qty = rng.choice(QTYS)
        unit = Decimal(rng.randint(low * 100, high * 100)) / 100
        lines.append((desc, qty, money(unit), money(unit * qty)))
    subtotal = money(sum((line[3] for line in lines), Decimal("0")))
    rate = Decimal(rng.choice(TAX_RATES))
    tax = money(subtotal * rate)
    total = subtotal + tax + (mismatch or Decimal("0"))

    street = f"{rng.randint(2, 4800)} {rng.choice(STREETS)}"
    city, state, zipcode = rng.choice(CITIES)
    slug = supplier.split()[0].lower()
    return {
        "id": f"{index:03d}-{slug}",
        "kind": kind,
        "layout": layout,
        "render": render,
        "supplier": supplier,
        "address": (street, f"{city}, {state} {zipcode}"),
        "invoice_number": number,
        "invoice_date": issued,
        "due_date": due,
        "terms": terms if show_terms else None,
        "po": f"PO {rng.randint(10000, 89999)}",
        "order_date": offset(rng, issued, -12, -2, numeric),
        "customer_id": f"C-{rng.randint(1000, 9999)}",
        "lines": lines,
        "rate": rate,
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
    }


# ---------- formatting ----------

def esc(text) -> str:
    return escape(str(text))


def usd(value: Decimal) -> str:
    return f"${value:,.2f}"


def plain(value: Decimal) -> str:
    return f"{value:,.2f}"


def iso(day: date) -> str:
    return day.isoformat()


def long_us(day: date) -> str:
    return f"{day.strftime('%B')} {day.day}, {day.year}"


def abbr_us(day: date) -> str:
    return f"{day.strftime('%b')} {day.day}, {day.year}"


def short_us(day: date) -> str:
    return day.strftime("%m/%d/%Y")


def day_month(day: date) -> str:
    return f"{day.day} {day.strftime('%b')} {day.year}"


def day_long(day: date) -> str:
    return f"{day.day} {day.strftime('%B')} {day.year}"


def dmy(day: date) -> str:
    return day.strftime("%d/%m/%Y")


def dash_dmy(day: date) -> str:
    return day.strftime("%d-%b-%Y")


def pct(rate: Decimal) -> str:
    text = f"{rate * 100:.2f}".rstrip("0").rstrip(".")
    return f"{text}%"


def style(font="Helvetica", size=10, leading=None, align=TA_LEFT, color=colors.black) -> ParagraphStyle:
    return ParagraphStyle(
        f"{font}-{size}-{align}", fontName=font, fontSize=size,
        leading=leading or size * 1.25, alignment=align, textColor=color,
    )


def p(text, st: ParagraphStyle) -> Paragraph:
    return Paragraph(text, st)


TIGHT = [
    ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ("TOPPADDING", (0, 0), (-1, -1), 1),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
]


def pairs(rows, label_st, value_st, widths, extra=()) -> Table:
    table = Table([[p(esc(a), label_st), p(esc(b), value_st)] for a, b in rows], colWidths=widths)
    table.setStyle(TableStyle(TIGHT + list(extra)))
    return table


def meta_rows(inv: dict, labels: dict, fmt, distractors=()) -> list[tuple[str, str]]:
    """Label/value rows for the invoice fields, skipping what is not printed."""
    rows = []
    if inv["invoice_number"]:
        rows.append((labels["number"], inv["invoice_number"]))
    rows.append((labels["date"], fmt(inv["invoice_date"])))
    for name in distractors:
        if name == "po":
            rows.append((labels.get("po", "PO Number"), inv["po"]))
        elif name == "order":
            rows.append((labels.get("order", "Order Date"), fmt(inv["order_date"])))
        elif name == "customer":
            rows.append((labels.get("customer", "Customer ID"), inv["customer_id"]))
    if inv["terms"]:
        rows.append((labels.get("terms", "Terms"), f"Net {inv['terms']}"))
    if inv["due_date"]:
        rows.append((labels["due"], fmt(inv["due_date"])))
    return rows


def address_block(lines) -> str:
    return "<br/>".join(esc(line) for line in lines)


# ---------- layouts ----------

def layout_ledger(inv):
    mono = style("Courier", 9.5, 12)
    bold = style("Courier-Bold", 9.5, 12)
    right = style("Courier", 9.5, 12, TA_RIGHT)
    rule_line = p("-" * 78, mono)
    story = [
        p(esc(inv["supplier"]), style("Courier-Bold", 15, 18)),
        p(address_block(inv["address"]), mono),
        Spacer(1, 10),
        rule_line,
        pairs(meta_rows(inv, {"number": "Inv #", "date": "Date", "due": "Due"}, iso), bold, mono,
              [1.2 * inch, 3 * inch]),
        rule_line,
        Spacer(1, 6),
        p("SOLD TO", bold),
        p(address_block(BUYER), mono),
        Spacer(1, 10),
    ]
    rows = [[p("QTY", bold), p("DESCRIPTION", bold), p("EACH", style("Courier-Bold", 9.5, 12, TA_RIGHT)),
             p("EXT", style("Courier-Bold", 9.5, 12, TA_RIGHT))]]
    for desc, qty, unit, amount in inv["lines"]:
        rows.append([p(str(qty), mono), p(esc(desc), mono), p(plain(unit), right), p(plain(amount), right)])
    grid = Table(rows, colWidths=[0.6 * inch, 3.9 * inch, 1.2 * inch, 1.3 * inch])
    grid.setStyle(TableStyle(TIGHT + [("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black)]))
    story += [grid, Spacer(1, 4), rule_line]
    totals = Table(
        [[p("Subtotal", bold), p(plain(inv["subtotal"]), right)],
         [p("Tax", bold), p(plain(inv["tax"]), right)],
         [p("Amount Due", bold), p(usd(inv["total"]), right)]],
        colWidths=[1.6 * inch, 1.4 * inch], hAlign="RIGHT",
    )
    totals.setStyle(TableStyle(TIGHT))
    story += [totals, Spacer(1, 18), p("Thank you for your business.", mono)]
    return story, letter


def layout_split_header(inv):
    base = style("Helvetica", 10)
    small = style("Helvetica", 8.5, 11, color=colors.HexColor("#555555"))
    label = style("Helvetica-Bold", 9, 12, TA_RIGHT)
    left = [p(esc(inv["supplier"]), style("Helvetica-Bold", 17, 20, color=colors.HexColor("#3b5323"))),
            Spacer(1, 3), p(address_block(inv["address"]), small)]
    meta = pairs(meta_rows(inv, {"number": "Invoice No.", "date": "Issued", "due": "Payment due"}, day_month),
                 label, style("Helvetica", 9.5, 12, TA_RIGHT), [1.3 * inch, 1.5 * inch])
    head = Table([[left, meta]], colWidths=[4.2 * inch, 2.9 * inch])
    head.setStyle(TableStyle(TIGHT + [("LINEBELOW", (0, 0), (-1, 0), 1.5, colors.HexColor("#3b5323")),
                                      ("BOTTOMPADDING", (0, 0), (-1, 0), 10)]))
    story = [head, Spacer(1, 16), p("<b>Customer</b>", base), p(address_block(BUYER), base), Spacer(1, 16)]
    hd = style("Helvetica-Bold", 9.5, 12)
    hdr = style("Helvetica-Bold", 9.5, 12, TA_RIGHT)
    num = style("Helvetica", 10, 13, TA_RIGHT)
    rows = [[p("Item", hd), p("Quantity", hdr), p("Rate", hdr), p("Line total", hdr)]]
    for desc, qty, unit, amount in inv["lines"]:
        rows.append([p(esc(desc), base), p(str(qty), num), p(usd(unit), num), p(usd(amount), num)])
    grid = Table(rows, colWidths=[3.6 * inch, 1 * inch, 1.2 * inch, 1.3 * inch])
    grid.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#bbbbbb")),
                              ("LINEBELOW", (0, 0), (-1, 0), 1, colors.black),
                              ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    totals = pairs([("Net amount", usd(inv["subtotal"])), ("Sales tax", usd(inv["tax"])),
                    ("Gross total", usd(inv["total"]))], label, num, [1.4 * inch, 1.3 * inch],
                   [("LINEABOVE", (0, 2), (-1, 2), 1, colors.black)])
    totals.hAlign = "RIGHT"
    story += [grid, Spacer(1, 12), totals]
    return story, letter


def layout_boxed_totals(inv):
    base = style("Helvetica", 10)
    gray = style("Helvetica", 8, 10, color=colors.HexColor("#666666"))
    story = [p(esc(inv["supplier"]), style("Helvetica-Bold", 21, 25)),
             p(esc("  ·  ".join(inv["address"])), gray), Spacer(1, 18)]
    cells = []
    if inv["invoice_number"]:
        cells.append(("Invoice #", inv["invoice_number"]))
    cells.append(("Invoice Date", long_us(inv["invoice_date"])))
    cells.append(("PO Number", inv["po"]))
    if inv["terms"]:
        cells.append(("Terms", f"Net {inv['terms']}"))
    if inv["due_date"]:
        cells.append(("Due Date", long_us(inv["due_date"])))
    strip = Table([[[p(esc(a.upper()), gray), p(esc(b), style("Helvetica-Bold", 10.5, 13))] for a, b in cells]],
                  colWidths=[7.1 * inch / len(cells)] * len(cells))
    strip.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.6, colors.black),
                               ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.black),
                               ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                               ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [strip, Spacer(1, 14), p("<b>Bill to:</b> " + esc(", ".join(BUYER)), base), Spacer(1, 14)]
    num = style("Helvetica", 10, 13, TA_RIGHT)
    head = style("Helvetica-Bold", 9, 12)
    rows = [[p("DESCRIPTION", head), p("QTY", head), p("PRICE", head), p("AMOUNT", head)]]
    for desc, qty, unit, amount in inv["lines"]:
        rows.append([p(esc(desc), base), p(str(qty), num), p(plain(unit), num), p(plain(amount), num)])
    grid = Table(rows, colWidths=[3.8 * inch, 0.8 * inch, 1.2 * inch, 1.3 * inch])
    grid.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e6e6e6")),
                              ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    lab = style("Helvetica", 10, 13)
    totals = Table([[p("Subtotal", lab), p(usd(inv["subtotal"]), num)],
                    [p("Tax", lab), p(usd(inv["tax"]), num)],
                    [p("<b>TOTAL DUE</b>", style("Helvetica-Bold", 12, 15)),
                     p(usd(inv["total"]), style("Helvetica-Bold", 12, 15, TA_RIGHT))]],
                   colWidths=[1.5 * inch, 1.4 * inch], hAlign="RIGHT")
    totals.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 2, colors.black),
                                ("LINEABOVE", (0, 2), (-1, 2), 1, colors.black),
                                ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                                ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8)]))
    story += [grid, Spacer(1, 16), totals]
    return story, letter


def layout_banner(inv):
    white_big = style("Helvetica-Bold", 18, 22, color=colors.white)
    band = Table([[p(esc(inv["supplier"]), white_big),
                   p("BILL", style("Helvetica-Bold", 22, 26, TA_RIGHT, colors.white))]],
                 colWidths=[5 * inch, 2.1 * inch])
    band.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#2b2b2b")),
                              ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                              ("TOPPADDING", (0, 0), (-1, -1), 12), ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                              ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12)]))
    base = style("Helvetica", 9.5, 12)
    bold = style("Helvetica-Bold", 9.5, 12)
    meta = pairs(meta_rows(inv, {"number": "Bill No.", "date": "Bill Date", "due": "Due By"}, short_us),
                 bold, base, [1.1 * inch, 1.6 * inch])
    top = Table([[p(address_block(inv["address"]), base), meta]], colWidths=[4.4 * inch, 2.7 * inch])
    top.setStyle(TableStyle(TIGHT))
    ship = list(BUYER[:1]) + ["Receiving dock", "1400 Warehouse Row", "Dayton, OH 45404"]
    who = Table([[[p("BILL TO", bold), p(address_block(BUYER), base)],
                  [p("SHIP TO", bold), p(address_block(ship), base)]]], colWidths=[3.55 * inch] * 2)
    who.setStyle(TableStyle(TIGHT))
    story = [band, Spacer(1, 10), top, Spacer(1, 14), who, Spacer(1, 14)]
    num = style("Helvetica", 9.5, 12, TA_RIGHT)
    rows = [[p("Qty", bold), p("Description", bold), p("Unit", bold), p("Amount", bold)]]
    for desc, qty, unit, amount in inv["lines"]:
        rows.append([p(str(qty), base), p(esc(desc), base), p(usd(unit), num), p(usd(amount), num)])
    grid = Table(rows, colWidths=[0.7 * inch, 4 * inch, 1.1 * inch, 1.3 * inch])
    grid.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, colors.black),
                              ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black),
                              ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    totals = pairs([("Items total", usd(inv["subtotal"])), ("Tax", usd(inv["tax"])),
                    ("Balance due", usd(inv["total"]))], style("Helvetica-Bold", 10, 14, TA_RIGHT),
                   style("Helvetica", 10, 14, TA_RIGHT), [1.4 * inch, 1.3 * inch])
    totals.hAlign = "RIGHT"
    story += [grid, Spacer(1, 10), totals]
    return story, letter


def layout_narrow_statement(inv):
    serif = style("Times-Roman", 10.5, 13.5)
    right = style("Times-Roman", 10.5, 13.5, TA_RIGHT)
    bold = style("Times-Bold", 10.5, 13.5)
    inner = [p(esc(inv["supplier"]), style("Times-Bold", 16, 19, TA_CENTER)),
             p(address_block(inv["address"]), style("Times-Roman", 9.5, 12, TA_CENTER)),
             Spacer(1, 6), p("Invoice", style("Times-Italic", 13, 16, TA_CENTER)), Spacer(1, 8)]
    width = [1.6 * inch, 1.9 * inch]
    inner.append(pairs(meta_rows(inv, {"number": "Reference", "date": "Dated", "due": "Payable by"}, day_long),
                       bold, right, width))
    inner += [Spacer(1, 8), p("To: " + esc(BUYER[0]), serif), Spacer(1, 8)]
    rows = []
    for desc, qty, unit, amount in inv["lines"]:
        rows.append([p(f"{esc(desc)}<br/><i>{qty} x {plain(unit)}</i>", serif), p(plain(amount), right)])
    lines = Table(rows, colWidths=width)
    lines.setStyle(TableStyle(TIGHT + [("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.grey),
                                       ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    inner += [lines, Spacer(1, 8),
              pairs([("Sum", plain(inv["subtotal"])), ("Tax", plain(inv["tax"])), ("To pay", usd(inv["total"]))],
                    bold, right, width, [("LINEABOVE", (0, 2), (-1, 2), 0.8, colors.black)])]
    frame = Table([[inner]], colWidths=[3.9 * inch])
    frame.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, colors.black),
                               ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                               ("TOPPADDING", (0, 0), (-1, -1), 14), ("BOTTOMPADDING", (0, 0), (-1, -1), 14)]))
    return [Spacer(1, 30), frame], letter


def layout_day_first(inv):
    base = style("Helvetica", 9.5, 12)
    bold = style("Helvetica-Bold", 9.5, 12)
    head = Table([[[p(esc(inv["supplier"]), style("Helvetica-Bold", 15, 18)),
                    p(address_block(inv["address"]), base)],
                   p("INVOICE", style("Helvetica", 24, 28, TA_RIGHT, colors.HexColor("#7a1f1f")))]],
                 colWidths=[4.6 * inch, 2.5 * inch])
    head.setStyle(TableStyle(TIGHT))
    meta = pairs(meta_rows(inv, {"number": "Invoice Ref", "date": "Invoice Date", "due": "Due Date",
                                 "order": "Order Date"}, dmy, distractors=("order",)),
                 bold, base, [1.2 * inch, 1.4 * inch])
    meta.hAlign = "RIGHT"
    story = [head, Spacer(1, 14), meta, Spacer(1, 12), p("<b>Invoice to</b>", base),
             p(address_block(BUYER), base), Spacer(1, 14)]
    num = style("Helvetica", 9.5, 12, TA_RIGHT)
    rows = [[p("Description", bold), p("Qty", bold), p("Unit price", bold), p("Amount (USD)", bold)]]
    for desc, qty, unit, amount in inv["lines"]:
        rows.append([p(esc(desc), base), p(str(qty), num), p(plain(unit), num), p(plain(amount), num)])
    grid = Table(rows, colWidths=[3.7 * inch, 0.7 * inch, 1.2 * inch, 1.5 * inch])
    grid.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#7a1f1f")),
                              ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    totals = pairs([("Sub-total", plain(inv["subtotal"])), ("VAT", plain(inv["tax"])),
                    ("Total due", plain(inv["total"]))], style("Helvetica-Bold", 10, 14, TA_RIGHT),
                   style("Helvetica", 10, 14, TA_RIGHT), [1.4 * inch, 1.5 * inch])
    totals.hAlign = "RIGHT"
    story += [grid, Spacer(1, 10), totals, Spacer(1, 18),
              p("Please quote the invoice reference with payment.", style("Helvetica", 8.5, 11))]
    return story, letter


def layout_rules_only(inv):
    base = style("Helvetica", 9.5, 12)
    bold = style("Helvetica-Bold", 9.5, 12)
    meta = pairs(meta_rows(inv, {"number": "Invoice #", "date": "Invoice Date", "due": "Due Date",
                                 "po": "PO #", "order": "Ship Date"}, abbr_us, distractors=("po", "order")),
                 bold, base, [1.1 * inch, 1.6 * inch])
    logo = [p(esc(inv["supplier"]), style("Helvetica-Bold", 16, 19, TA_RIGHT, colors.HexColor("#0b5563"))),
            p(address_block(inv["address"]), style("Helvetica", 9, 11.5, TA_RIGHT))]
    head = Table([[meta, logo]], colWidths=[3.2 * inch, 3.9 * inch])
    head.setStyle(TableStyle(TIGHT))
    story = [head, Spacer(1, 18), p("<b>Billed to</b>", base), p(address_block(BUYER), base), Spacer(1, 16)]
    num = style("Helvetica", 9.5, 12, TA_RIGHT)
    numb = style("Helvetica-Bold", 9.5, 12, TA_RIGHT)
    rows = [[p("Description", bold), p("Qty", numb), p("Price", numb), p("Total", numb)]]
    for desc, qty, unit, amount in inv["lines"]:
        rows.append([p(esc(desc), base), p(str(qty), num), p(usd(unit), num), p(usd(amount), num)])
    grid = Table(rows, colWidths=[3.8 * inch, 0.8 * inch, 1.2 * inch, 1.3 * inch])
    grid.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 1, colors.HexColor("#0b5563")),
                              ("LINEBELOW", (0, 0), (-1, 0), 1, colors.HexColor("#0b5563")),
                              ("LINEBELOW", (0, -1), (-1, -1), 1, colors.HexColor("#0b5563")),
                              ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    totals = pairs([("Subtotal", usd(inv["subtotal"])), (f"Sales Tax ({pct(inv['rate'])})", usd(inv["tax"])),
                    ("Total", usd(inv["total"]))], numb, num, [1.6 * inch, 1.3 * inch])
    totals.hAlign = "RIGHT"
    story += [grid, Spacer(1, 10), totals]
    return story, letter


def layout_sidebar(inv):
    small = style("Helvetica", 8, 10, color=colors.HexColor("#444444"))
    value = style("Helvetica-Bold", 10, 13)
    side = [p("INVOICE", style("Helvetica-Bold", 14, 17, color=colors.HexColor("#5a3e85"))), Spacer(1, 12)]
    for label, text in meta_rows(inv, {"number": "Document no.", "date": "Date of issue", "due": "Pay by"},
                                 dash_dmy, distractors=("customer",)):
        side += [p(esc(label), small), p(esc(text), value), Spacer(1, 8)]
    base = style("Helvetica", 9.5, 12)
    bold = style("Helvetica-Bold", 9.5, 12)
    num = style("Helvetica", 9.5, 12, TA_RIGHT)
    rows = [[p("Description", bold), p("Qty", bold), p("Each", bold), p("Amount", bold)]]
    for desc, qty, unit, amount in inv["lines"]:
        rows.append([p(esc(desc), base), p(str(qty), num), p(plain(unit), num), p(plain(amount), num)])
    grid = Table(rows, colWidths=[2.4 * inch, 0.5 * inch, 0.9 * inch, 1 * inch])
    grid.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#999999")),
                              ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    totals = pairs([("Goods", usd(inv["subtotal"])), ("Tax", usd(inv["tax"])),
                    ("Amount payable", usd(inv["total"]))], style("Helvetica-Bold", 10, 14, TA_RIGHT),
                   style("Helvetica", 10, 14, TA_RIGHT), [1.4 * inch, 1.1 * inch])
    totals.hAlign = "RIGHT"
    main = [p(esc(inv["supplier"]), style("Helvetica-Bold", 17, 20)), p(address_block(inv["address"]), base),
            Spacer(1, 20), p("<b>Invoice to</b>", base), p(address_block(BUYER), base), Spacer(1, 18),
            grid, Spacer(1, 10), totals]
    page = Table([[side, main]], colWidths=[1.9 * inch, 5.2 * inch])
    page.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#efeaf6")),
                              ("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("LEFTPADDING", (0, 0), (0, 0), 12), ("TOPPADDING", (0, 0), (-1, -1), 14),
                              ("LEFTPADDING", (1, 0), (1, 0), 18), ("BOTTOMPADDING", (0, 0), (-1, -1), 30)]))
    return [page], letter


def layout_inline_totals(inv):
    base = style("Helvetica", 9.5, 12)
    bold = style("Helvetica-Bold", 9.5, 12)
    story = [p(esc(inv["supplier"]), style("Helvetica-Bold", 18, 22)),
             p("<i>Serving local business since 1987</i>", style("Helvetica-Oblique", 9, 11)),
             p(esc(", ".join(inv["address"])), base), Spacer(1, 14)]
    cells = []
    if inv["invoice_number"]:
        cells.append(f"<b>No.</b> {esc(inv['invoice_number'])}")
    cells.append(f"<b>Date</b> {short_us(inv['invoice_date'])}")
    if inv["due_date"]:
        cells.append(f"<b>Due</b> {short_us(inv['due_date'])}")
    if inv["terms"]:
        cells.append(f"<b>Terms</b> Net {inv['terms']}")
    strip = Table([[p(cell, base) for cell in cells]], colWidths=[7.1 * inch / len(cells)] * len(cells))
    strip.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, colors.black),
                               ("INNERGRID", (0, 0), (-1, -1), 0.6, colors.black),
                               ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story += [strip, Spacer(1, 12), p("<b>Customer:</b> " + esc(", ".join(BUYER)), base), Spacer(1, 12)]
    num = style("Helvetica", 9.5, 12, TA_RIGHT)
    numb = style("Helvetica-Bold", 9.5, 12, TA_RIGHT)
    rows = [[p("Description", bold), p("Qty", numb), p("Unit", numb), p("Amount", numb)]]
    for desc, qty, unit, amount in inv["lines"]:
        rows.append([p(esc(desc), base), p(str(qty), num), p(plain(unit), num), p(plain(amount), num)])
    first_total = len(rows)
    rows.append(["", "", p("Subtotal", numb), p(plain(inv["subtotal"]), num)])
    rows.append(["", "", p("Sales Tax", numb), p(plain(inv["tax"]), num)])
    rows.append(["", "", p("Total", numb), p(usd(inv["total"]), numb)])
    grid = Table(rows, colWidths=[3.8 * inch, 0.8 * inch, 1.2 * inch, 1.3 * inch])
    grid.setStyle(TableStyle([("GRID", (0, 0), (-1, first_total - 1), 0.5, colors.black),
                              ("GRID", (2, first_total), (-1, -1), 0.5, colors.black),
                              ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dddddd")),
                              ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story += [grid]
    return story, letter


def layout_letter(inv):
    body = style("Helvetica", 10.5, 15)
    story = [p(esc(inv["supplier"]), style("Helvetica-Bold", 15, 18, TA_CENTER)),
             p(esc(" | ".join(inv["address"])), style("Helvetica", 9, 11, TA_CENTER)),
             Spacer(1, 26), p(f"Date: {long_us(inv['invoice_date'])}", style("Helvetica", 10.5, 15, TA_RIGHT)),
             Spacer(1, 10), p(address_block(BUYER), body), Spacer(1, 16)]
    if inv["invoice_number"]:
        story.append(p(f"<b>Re: Invoice {esc(inv['invoice_number'])}</b>", body))
    else:
        story.append(p("<b>Re: Goods and services supplied</b>", body))
    story += [Spacer(1, 10), p("Thank you for your order. The charges are listed below.", body), Spacer(1, 10)]
    num = style("Helvetica", 10, 13, TA_RIGHT)
    rows = [[p(f"{esc(desc)} ({qty} at {usd(unit)})", body), p(usd(amount), num)]
            for desc, qty, unit, amount in inv["lines"]]
    rows += [[p("Subtotal", style("Helvetica-Bold", 10, 13, TA_RIGHT)), p(usd(inv["subtotal"]), num)],
             [p("Tax", style("Helvetica-Bold", 10, 13, TA_RIGHT)), p(usd(inv["tax"]), num)],
             [p("Amount Due", style("Helvetica-Bold", 10, 13, TA_RIGHT)),
              p(usd(inv["total"]), style("Helvetica-Bold", 10, 13, TA_RIGHT))]]
    grid = Table(rows, colWidths=[5 * inch, 1.5 * inch])
    grid.setStyle(TableStyle(TIGHT + [("LINEABOVE", (0, len(inv["lines"])), (-1, len(inv["lines"])), 0.5,
                                       colors.black), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    story += [grid, Spacer(1, 14)]
    if inv["due_date"]:
        story.append(p(f"Please pay the amount due by {long_us(inv['due_date'])}.", body))
    elif inv["terms"]:
        story.append(p(f"Payment terms are net {inv['terms']} days.", body))
    story += [Spacer(1, 22), p("Sincerely,", body), Spacer(1, 18), p(esc(inv["supplier"]), body)]
    return story, letter


def layout_landscape(inv):
    base = style("Helvetica", 10, 13)
    bold = style("Helvetica-Bold", 10, 13)
    meta = pairs(meta_rows(inv, {"number": "Invoice Number:", "date": "Invoice Date:", "due": "Due Date:"},
                           long_us), bold, base, [1.3 * inch, 1.6 * inch])
    head = Table([[[p(esc(inv["supplier"]), style("Helvetica-Bold", 17, 20)), p(address_block(inv["address"]), base)],
                   p("INVOICE", style("Helvetica-Bold", 26, 30, TA_CENTER, colors.HexColor("#8a6d1f"))),
                   meta]], colWidths=[3.6 * inch, 2.6 * inch, 3.1 * inch])
    head.setStyle(TableStyle(TIGHT + [("LINEBELOW", (0, 0), (-1, 0), 2, colors.HexColor("#8a6d1f")),
                                      ("BOTTOMPADDING", (0, 0), (-1, 0), 10)]))
    story = [head, Spacer(1, 12), p("<b>Bill to:</b> " + esc(", ".join(BUYER)), base), Spacer(1, 12)]
    num = style("Helvetica", 10, 13, TA_RIGHT)
    numb = style("Helvetica-Bold", 10, 13, TA_RIGHT)
    rows = [[p("SKU", bold), p("Description", bold), p("Qty", numb), p("Unit price", numb), p("Amount", numb)]]
    for line_no, (desc, qty, unit, amount) in enumerate(inv["lines"], 1):
        sku = f"{inv['customer_id'][2:]}-{line_no:02d}"
        rows.append([p(sku, base), p(esc(desc), base), p(str(qty), num), p(usd(unit), num), p(usd(amount), num)])
    grid = Table(rows, colWidths=[1.1 * inch, 4.4 * inch, 0.9 * inch, 1.4 * inch, 1.5 * inch])
    grid.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#999999")),
                              ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3ecd9")),
                              ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    totals = pairs([("Subtotal", usd(inv["subtotal"])), ("Tax", usd(inv["tax"])), ("Total", usd(inv["total"]))],
                   numb, num, [1.4 * inch, 1.5 * inch], [("LINEABOVE", (0, 2), (-1, 2), 1, colors.black)])
    totals.hAlign = "RIGHT"
    story += [grid, Spacer(1, 10), totals]
    return story, landscape(letter)


def layout_summary_first(inv):
    base = style("Helvetica", 10, 13)
    bold = style("Helvetica-Bold", 10, 13)
    box = [p("Amount Due", style("Helvetica", 10, 13, TA_RIGHT)),
           p(usd(inv["total"]), style("Helvetica-Bold", 20, 24, TA_RIGHT))]
    if inv["due_date"]:
        box += [Spacer(1, 4), p(f"Due Date: {long_us(inv['due_date'])}", style("Helvetica", 10, 13, TA_RIGHT))]
    elif inv["terms"]:
        box += [Spacer(1, 4), p(f"Terms: Net {inv['terms']}", style("Helvetica", 10, 13, TA_RIGHT))]
    summary = Table([[box]], colWidths=[2.6 * inch])
    summary.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#1d4e89")),
                                 ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eaf1fa")),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                                 ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    head = Table([[[p(esc(inv["supplier"]), style("Helvetica-Bold", 16, 19)), p(address_block(inv["address"]), base)],
                   summary]], colWidths=[4.5 * inch, 2.6 * inch])
    head.setStyle(TableStyle(TIGHT))
    rows = []
    if inv["invoice_number"]:
        rows.append(("Invoice no.", inv["invoice_number"]))
    rows.append(("Invoice date", long_us(inv["invoice_date"])))
    rows.append(("Account", inv["customer_id"]))
    story = [head, Spacer(1, 18), pairs(rows, bold, base, [1.2 * inch, 2.4 * inch]), Spacer(1, 12),
             p("<b>Bill to</b>", base), p(address_block(BUYER), base), Spacer(1, 14)]
    num = style("Helvetica", 10, 13, TA_RIGHT)
    numb = style("Helvetica-Bold", 10, 13, TA_RIGHT)
    lines = [[p("Item", bold), p("Qty", numb), p("Rate", numb), p("Amount", numb)]]
    for desc, qty, unit, amount in inv["lines"]:
        lines.append([p(esc(desc), base), p(str(qty), num), p(usd(unit), num), p(usd(amount), num)])
    grid = Table(lines, colWidths=[3.8 * inch, 0.8 * inch, 1.2 * inch, 1.3 * inch])
    grid.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 1, colors.HexColor("#1d4e89")),
                              ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    totals = pairs([("Subtotal", usd(inv["subtotal"])), ("Tax", usd(inv["tax"])), ("Total", usd(inv["total"]))],
                   numb, num, [1.4 * inch, 1.3 * inch], [("LINEABOVE", (0, 2), (-1, 2), 1, colors.black)])
    totals.hAlign = "RIGHT"
    story += [grid, Spacer(1, 10), totals]
    return story, letter


BUILDERS = {
    "ledger": layout_ledger,
    "split-header": layout_split_header,
    "boxed-totals": layout_boxed_totals,
    "banner": layout_banner,
    "narrow-statement": layout_narrow_statement,
    "day-first": layout_day_first,
    "rules-only": layout_rules_only,
    "sidebar": layout_sidebar,
    "inline-totals": layout_inline_totals,
    "letter": layout_letter,
    "landscape": layout_landscape,
    "summary-first": layout_summary_first,
}


# ---------- writing ----------

def write_pdf(inv: dict, path: Path) -> None:
    story, pagesize = BUILDERS[inv["layout"]](inv)
    path.parent.mkdir(parents=True, exist_ok=True)
    title = f"{inv['supplier']} {inv['invoice_number'] or ''}".strip()
    SimpleDocTemplate(
        str(path), pagesize=pagesize, leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch, title=title, author=inv["supplier"],
    ).build(story)
    with pymupdf.open(path) as doc:
        if doc.page_count != 1:
            raise SystemExit(f"{inv['id']} ({inv['layout']}): {doc.page_count} pages, expected one")


def label_row(inv: dict, file: str, digest: str) -> dict:
    return {
        "id": inv["id"],
        "file": file,
        "supplier": inv["supplier"],
        "invoice_number": inv["invoice_number"],
        "invoice_date": iso(inv["invoice_date"]),
        "due_date": iso(inv["due_date"]) if inv["due_date"] else None,
        "subtotal": f"{inv['subtotal']:.2f}",
        "tax": f"{inv['tax']:.2f}",
        "total": f"{inv['total']:.2f}",
        "exception": bool(EXPECT[inv["kind"]]),
        "exception_reasons": list(EXPECT[inv["kind"]]),
        "layout": inv["layout"],
        "render": inv["render"],
        "sha256": digest,
    }


def plan(rng: random.Random) -> list[dict]:
    """Stratified: every layout gets two or three exceptions and at least
    two scans and two photos, so no layout is seen in one form only."""
    kinds = KINDS[:]
    rng.shuffle(kinds)
    kinds.sort(key=lambda kind: kind == "clean")
    order = LAYOUTS[:]
    rng.shuffle(order)
    dealt = [(kind, order[i % len(order)]) for i, kind in enumerate(kinds)]

    per_layout: dict[str, list[str]] = {}
    for layout in order:
        size = sum(1 for _, dealt_layout in dealt if dealt_layout == layout)
        per_layout[layout] = ["scan", "scan", "photo", "photo"] + ["clean"] * (size - 4)
    spare = {"scan": RENDERS.count("scan"), "photo": RENDERS.count("photo")}
    for render in spare:
        spare[render] -= sum(renders.count(render) for renders in per_layout.values())
    nines = [layout for layout in order if len(per_layout[layout]) == 9]
    for render, layout in zip(["scan"] * spare["scan"] + ["photo"] * spare["photo"], nines):
        per_layout[layout][per_layout[layout].index("clean")] = render
    for renders in per_layout.values():
        rng.shuffle(renders)
    if sorted(r for renders in per_layout.values() for r in renders) != sorted(RENDERS):
        raise SystemExit("render quotas do not add up")

    slots = [(kind, layout, per_layout[layout].pop()) for kind, layout in dealt]
    rng.shuffle(slots)
    names = suppliers(rng, len(slots))
    deltas = iter(MISMATCH)
    invoices = []
    for index, ((kind, layout, render), name) in enumerate(zip(slots, names), 1):
        delta = next(deltas) if kind == "mismatch" else None
        invoices.append(build_invoice(rng, index, name, kind, layout, render, delta))
    return invoices


def main() -> None:
    if LABELS.exists():
        raise SystemExit(f"{LABELS.relative_to(ROOT)} exists. The holdout is frozen; do not regenerate it.")
    rng = random.Random(SEED)
    invoices = plan(rng)
    labels = []
    with tempfile.TemporaryDirectory() as scratch:
        for inv in invoices:
            file = f"holdout/{FOLDERS[inv['render']]}/{inv['id']}.pdf"
            dest = ROOT / file
            if inv["render"] == "clean":
                write_pdf(inv, dest)
            else:
                source = Path(scratch) / f"{inv['id']}.pdf"
                write_pdf(inv, source)
                dest.parent.mkdir(parents=True, exist_ok=True)
                (render_scan if inv["render"] == "scan" else render_photo)(source, dest)
            row = label_row(inv, file, hashlib.sha256(dest.read_bytes()).hexdigest())
            found = rule(row)
            if found != row["exception_reasons"]:
                raise SystemExit(f"{inv['id']}: rule says {found}, kind {inv['kind']} expects {row['exception_reasons']}")
            labels.append(row)
    payload = {
        "fields": list(FIELDS),
        "frozen": FROZEN,
        "generator": "make_holdout.py",
        "seed": SEED,
        "layouts": LAYOUTS,
        "invoices": labels,
    }
    LABELS.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    exceptions = sum(1 for row in labels if row["exception"])
    print(f"wrote {len(labels)} invoices in {len(LAYOUTS)} layouts, {exceptions} need an exception")


if __name__ == "__main__":
    main()
