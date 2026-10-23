"""Write the harder invoice-ap set under hard/.

Does not rewrite fixtures/ or labels.json. Three groups, kept apart:

- scans/: the ten generated invoices, grayscale, skewed, grainy
- photos/: those same ten, as a phone picture of the page
- odd/: four new invoices in a second layout

Labels are hard/labels.json. Needs pymupdf, pillow, and reportlab
(sandbox/.venv).
"""

from __future__ import annotations

import io
import json
import zlib
from datetime import datetime
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageChops, ImageFilter
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib import colors

from make_fixtures import BUYER, FIELDS, line_amount, money, money_pdf, reasons_for, subtotal_of

ROOT = Path(__file__).resolve().parent
CLEAN_LABELS = ROOT / "labels.json"
HARD = ROOT / "hard"
DESK = (214, 206, 196)


ODD = [
    {
        "id": "odd-lumen",
        "supplier": "Lumen Glassworks",
        "address": ("14 Kiln Road", "Akron, OH 44308"),
        "invoice_number": "LG-1904",
        "invoice_date": "2026-06-02",
        "due_date": "2026-07-02",
        "lines": (
            ("Annealed pane, 24x36", "4", "86.00"),
            ("Glazing tape", "2", "12.50"),
        ),
        "tax": "29.52",
        "total": "398.52",
        "expect": [],
    },
    {
        "id": "odd-marrow",
        "supplier": "Marrow Freight",
        "address": ("800 Transfer Court", "Gary, IN 46402"),
        "invoice_number": "MF-6620",
        "invoice_date": "2026-07-11",
        "due_date": None,
        "lines": (("Pallet move", "3", "75.00"),),
        "tax": "18.00",
        "total": "243.00",
        "expect": ["missing_due_date"],
    },
    {
        "id": "odd-sable",
        "supplier": "Sable Print Shop",
        "address": ("2 Press Alley", "Madison, WI 53703"),
        "invoice_number": "SP-44",
        "invoice_date": "2026-08-20",
        "due_date": "2026-08-01",
        "lines": (("Poster run", "1", "640.00"),),
        "tax": "0.00",
        "total": "640.00",
        "expect": ["due_before_invoice"],
    },
    {
        "id": "odd-quarry",
        "supplier": "Quarry Salt Co.",
        "address": ("50 Pit Road", "Hutchinson, KS 67501"),
        "invoice_number": "QS-3188",
        "invoice_date": "2026-09-04",
        "due_date": "2026-10-04",
        "lines": (("Rock salt, bag", "50", "8.40"),),
        "tax": "33.60",
        "total": "430.00",
        "expect": ["total_mismatch"],
    },
]


def render_page(pdf_path: Path, dpi: int) -> Image.Image:
    doc = pymupdf.open(pdf_path)
    try:
        if doc.page_count != 1:
            raise SystemExit(f"{pdf_path.name}: expected one page, got {doc.page_count}")
        pix = doc[0].get_pixmap(dpi=dpi, alpha=False)
    finally:
        doc.close()
    mode = "RGB" if pix.n == 3 else "L"
    return Image.frombytes(mode, (pix.width, pix.height), pix.samples).convert("RGB")


def save_pdf(img: Image.Image, path: Path, dpi: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(path, "PDF", resolution=dpi)


def grain(img: Image.Image, sigma: float, seed: int) -> Image.Image:
    arr = np.asarray(img.convert("L"), dtype=np.int16)
    noise = np.random.default_rng(seed).normal(0, sigma, arr.shape)
    out = np.clip(arr + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(out, mode="L")


def render_scan(source: Path, dest: Path) -> None:
    img = render_page(source, 150).convert("L")
    img = img.rotate(0.8, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255)
    img = img.filter(ImageFilter.GaussianBlur(radius=0.35))
    img = grain(img, 11, zlib.crc32(source.name.encode()))
    save_pdf(img, dest, 150)


def find_coeffs(source_coords, target_coords):
    matrix = []
    for src, tgt in zip(source_coords, target_coords):
        matrix.append([tgt[0], tgt[1], 1, 0, 0, 0, -src[0] * tgt[0], -src[0] * tgt[1]])
        matrix.append([0, 0, 0, tgt[0], tgt[1], 1, -src[1] * tgt[0], -src[1] * tgt[1]])
    solved = np.linalg.solve(np.array(matrix, dtype=np.float64), np.array(source_coords).reshape(8))
    return solved.reshape(8).tolist()


def perspective(img: Image.Image) -> Image.Image:
    width, height = img.size
    src = [(0, 0), (width, 0), (width, height), (0, height)]
    dst = [
        (28, 6),
        (width - 10, 34),
        (width - 36, height - 8),
        (14, height - 42),
    ]
    coeffs = find_coeffs(src, dst)
    return img.transform(
        (width, height),
        Image.PERSPECTIVE,
        coeffs,
        Image.Resampling.BICUBIC,
        fillcolor=DESK,
    )


def shade(img: Image.Image) -> Image.Image:
    band = Image.new("L", (1, 8))
    for index, value in enumerate((188, 214, 236, 248, 242, 220, 184, 150)):
        band.putpixel((0, index), value)
    mask = band.resize(img.size, Image.Resampling.BILINEAR)
    return ImageChops.multiply(img, Image.merge("RGB", (mask, mask, mask)))


def warm(img: Image.Image) -> Image.Image:
    red, green, blue = img.split()
    red = red.point(lambda px: min(255, int(px * 1.05 + 6)))
    blue = blue.point(lambda px: max(0, int(px * 0.90)))
    return Image.merge("RGB", (red, green, blue))


def render_photo(source: Path, dest: Path) -> None:
    page = render_page(source, 110)
    width, height = page.size
    canvas = Image.new("RGB", (width + 120, height + 150), DESK)
    canvas.paste(page, (46, 58))
    canvas = perspective(canvas)
    canvas = shade(canvas)
    canvas = warm(canvas)
    canvas = canvas.rotate(-3.2, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=DESK)
    encoded = io.BytesIO()
    canvas.save(encoded, "JPEG", quality=46)
    encoded.seek(0)
    with Image.open(encoded) as shot:
        shot.load()
        save_pdf(shot, dest, 110)


def us_date(iso: str) -> str:
    day = datetime.strptime(iso, "%Y-%m-%d")
    return day.strftime("%m/%d/%Y")


def odd_styles():
    base = ParagraphStyle(
        "base",
        fontName="Times-Roman",
        fontSize=11,
        leading=14,
        textColor=colors.black,
        alignment=TA_LEFT,
    )
    return {
        "supplier": ParagraphStyle(
            "supplier",
            parent=base,
            fontName="Times-Bold",
            fontSize=18,
            leading=21,
            alignment=TA_CENTER,
        ),
        "address": ParagraphStyle(
            "address",
            parent=base,
            fontName="Times-Roman",
            fontSize=10,
            leading=13,
            alignment=TA_CENTER,
        ),
        "label": ParagraphStyle("label", parent=base, fontName="Times-Bold", fontSize=11, leading=14),
        "value": ParagraphStyle("value", parent=base, fontName="Times-Roman", fontSize=11, leading=14),
        "lines": ParagraphStyle("lines", parent=base, fontName="Times-Roman", fontSize=11, leading=15),
        "money_label": ParagraphStyle(
            "money_label",
            parent=base,
            fontName="Times-Bold",
            fontSize=11,
            leading=14,
            alignment=TA_RIGHT,
        ),
        "money": ParagraphStyle(
            "money",
            parent=base,
            fontName="Times-Roman",
            fontSize=11,
            leading=14,
            alignment=TA_RIGHT,
        ),
        "small": ParagraphStyle("small", parent=base, fontName="Times-Italic", fontSize=9, leading=12),
    }


def write_odd(invoice: dict, subtotal, path: Path) -> None:
    look = odd_styles()
    story = [
        Paragraph(invoice["supplier"], look["supplier"]),
        Spacer(1, 2),
        Paragraph("<br/>".join(invoice["address"]), look["address"]),
        Spacer(1, 8),
    ]
    meta = [
        [Paragraph("Invoice number", look["label"]), Paragraph(invoice["invoice_number"], look["value"])],
        [Paragraph("Invoice date", look["label"]), Paragraph(us_date(invoice["invoice_date"]), look["value"])],
    ]
    if invoice["due_date"]:
        meta.append(
            [Paragraph("Due date", look["label"]), Paragraph(us_date(invoice["due_date"]), look["value"])]
        )
    meta_table = Table(meta, colWidths=[1.6 * inch, 2.2 * inch], hAlign="LEFT")
    meta_table.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 16))

    line_bits = []
    for desc, qty, unit in invoice["lines"]:
        amount = line_amount(qty, unit)
        line_bits.append(
            f"{desc}<br/>{qty} @ {money_pdf(money(unit))} &nbsp;&nbsp; {money_pdf(amount)}"
        )
    body = Table(
        [[
            [
                Paragraph("Sold to", look["label"]),
                Spacer(1, 3),
                Paragraph("<br/>".join(BUYER), look["value"]),
            ],
            Paragraph("<br/><br/>".join(line_bits), look["lines"]),
        ]],
        colWidths=[3.2 * inch, 3.6 * inch],
    )
    body.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("LINEBEFORE", (1, 0), (1, 0), 0.4, colors.black),
        ("LEFTPADDING", (1, 0), (1, 0), 12),
    ]))
    story.append(body)
    story.append(Spacer(1, 18))

    totals = [
        [Paragraph("Subtotal", look["money_label"]), Paragraph(money_pdf(subtotal), look["money"])],
        [Paragraph("Tax", look["money_label"]), Paragraph(money_pdf(money(invoice["tax"])), look["money"])],
        [Paragraph("Total", look["money_label"]), Paragraph(money_pdf(money(invoice["total"])), look["money"])],
    ]
    totals_table = Table(totals, colWidths=[1.3 * inch, 1.2 * inch], hAlign="LEFT")
    totals_table.setStyle(TableStyle([
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(totals_table)
    story.append(Spacer(1, 22))
    story.append(Paragraph("Pay from this statement.", look["small"]))

    path.parent.mkdir(parents=True, exist_ok=True)

    def stamp(canvas, _doc):
        canvas.setTitle(f"{invoice['supplier']} {invoice['invoice_number']}")
        canvas.setAuthor(invoice["supplier"])

    SimpleDocTemplate(
        str(path),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.65 * inch,
        title=f"{invoice['supplier']} {invoice['invoice_number']}",
        author=invoice["supplier"],
    ).build(story, onFirstPage=stamp)


def retarget(row: dict, kind: str, folder: str) -> dict:
    copied = dict(row)
    copied["id"] = f"{kind}-{row['id']}"
    copied["file"] = f"hard/{folder}/{copied['id']}.pdf"
    copied["exception_reasons"] = list(row["exception_reasons"])
    return copied


def odd_row(invoice: dict, subtotal, found: list[str]) -> dict:
    return {
        "id": invoice["id"],
        "file": f"hard/odd/{invoice['id']}.pdf",
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
    clean = json.loads(CLEAN_LABELS.read_text(encoding="utf-8"))["invoices"]
    labels = []
    for row in clean:
        source = ROOT / row["file"]
        if not source.is_file():
            raise SystemExit(f"missing {source}")
        scan = retarget(row, "scan", "scans")
        photo = retarget(row, "photo", "photos")
        render_scan(source, ROOT / scan["file"])
        render_photo(source, ROOT / photo["file"])
        labels.append(scan)
        labels.append(photo)
    for invoice in ODD:
        subtotal = subtotal_of(invoice)
        found = reasons_for(invoice, subtotal)
        if found != invoice["expect"]:
            raise SystemExit(f"{invoice['id']}: reasons {found} != {invoice['expect']}")
        row = odd_row(invoice, subtotal, found)
        write_odd(invoice, subtotal, ROOT / row["file"])
        labels.append(row)
    payload = {"fields": list(FIELDS), "invoices": labels}
    dest = HARD / "labels.json"
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    exceptions = sum(1 for row in labels if row["exception"])
    print(f"wrote {len(labels)} invoices, {exceptions} need an exception")


if __name__ == "__main__":
    main()
