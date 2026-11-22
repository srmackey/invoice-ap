#!/usr/bin/env python3
"""
make_set.py - Deterministic generator for cross-border invoice test set.
Fixed seed, labels from same values used to render.
"""
import json
import hashlib
import os
import random
import re
from datetime import datetime, timedelta
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.units import inch, mm
from reportlab.lib.colors import HexColor, black, white, Color
from pdf2image import convert_from_path
from PIL import Image, ImageFilter, ImageEnhance, ImageDraw
import numpy as np

SEED = 0
rng = random.Random(SEED)
np.random.seed(SEED)

OUT = "/workspace/artifacts"
for d in ["clean", "scans", "photos"]:
    os.makedirs(os.path.join(OUT, d), exist_ok=True)

# ---- Data definitions ----
COUNTRIES = {
    "US": {"date": "month-first", "num": "1,234.56", "ps": letter, "tax": "Sales Tax", "curr": "USD", "lang": "en"},
    "CA": {"date": "day-first", "num": "1,234.56", "ps": letter, "tax": "GST/HST", "curr": "CAD", "lang": "en/fr"},
    "GB": {"date": "day-first", "num": "1,234.56", "ps": A4, "tax": "VAT", "curr": "GBP", "lang": "en"},
    "IE": {"date": "day-first", "num": "1,234.56", "ps": A4, "tax": "VAT", "curr": "EUR", "lang": "en"},
    "DE": {"date": "day-first", "num": "1.234,56", "ps": A4, "tax": "MwSt", "curr": "EUR", "lang": "de"},
    "FR": {"date": "day-first", "num": "1 234,56", "ps": A4, "tax": "TVA", "curr": "EUR", "lang": "fr"},
    "NL": {"date": "day-first", "num": "1.234,56", "ps": A4, "tax": "BTW", "curr": "EUR", "lang": "nl"},
    "IT": {"date": "day-first", "num": "1.234,56", "ps": A4, "tax": "IVA", "curr": "EUR", "lang": "it"},
    "ES": {"date": "day-first", "num": "1.234,56", "ps": A4, "tax": "IVA", "curr": "EUR", "lang": "es"},
}

SUPPLIERS = [
    # US (about 1/5 of 60 = 12, so ~4 suppliers x 3)
    {"name": "Summit Components Inc", "cc": "US", "layout": "standard_right_totals", "trade": "industrial parts"},
    {"name": "Pacific Digital Solutions LLC", "cc": "US", "layout": "modern_left_header", "trade": "software services"},
    {"name": "Heartland Logistics Corp", "cc": "US", "layout": "classic_centered", "trade": "freight"},
    {"name": "Lone Star Materials Co", "cc": "US", "layout": "standard_right_totals", "trade": "raw materials"},
    # CA
    {"name": "Northwood Supply Ltd", "cc": "CA", "layout": "bilingual_ca", "trade": "building supplies"},
    {"name": "Maple Precision Engineering", "cc": "CA", "layout": "standard_right_totals", "trade": "machining"},
    {"name": "Aurora Consulting Group", "cc": "CA", "layout": "modern_left_header", "trade": "consulting"},
    # GB
    {"name": "Thames Valley Systems Ltd", "cc": "GB", "layout": "uk_vat_bottom", "trade": "IT hardware"},
    {"name": "Highland Fabrication", "cc": "GB", "layout": "classic_centered", "trade": "fabrication"},
    # IE
    {"name": "Emerald Data Services", "cc": "IE", "layout": "modern_left_header", "trade": "data services"},
    {"name": "Celtic Components Ltd", "cc": "IE", "layout": "standard_right_totals", "trade": "components"},
    # DE
    {"name": "Alpen Maschinenbau GmbH", "cc": "DE", "layout": "eu_vat_iban", "trade": "machinery"},
    {"name": "Rhein Precision Parts", "cc": "DE", "layout": "classic_centered", "trade": "precision parts"},
    # FR
    {"name": "Atelier Lumiere SAS", "cc": "FR", "layout": "fr_tva_left", "trade": "lighting"},
    {"name": "Provence Agro SARL", "cc": "FR", "layout": "eu_vat_iban", "trade": "agro supplies"},
    # NL
    {"name": "Holland Tech BV", "cc": "NL", "layout": "nl_btw_modern", "trade": "tech solutions"},
    {"name": "Delta Logistics BV", "cc": "NL", "layout": "standard_right_totals", "trade": "logistics"},
    # IT
    {"name": "Milano Design SRL", "cc": "IT", "layout": "it_iva_elegant", "trade": "design"},
    {"name": "Roma Componenti SpA", "cc": "IT", "layout": "eu_vat_iban", "trade": "components"},
    # ES
    {"name": "Iberia Materiales SL", "cc": "ES", "layout": "es_iva_standard", "trade": "materials"},
    {"name": "Barcelona Textiles SA", "cc": "ES", "layout": "classic_centered", "trade": "textiles"},
]

LAYOUTS = sorted({s["layout"] for s in SUPPLIERS})

BUYER = {
    "name": "Northstar Fabrication LLC",
    "addr": "1842 Industrial Parkway",
    "city": "Broomfield, CO 80020",
    "country": "United States"
}

def fmt_num(amount, style):
    s = f"{amount:.2f}"
    ip, dp = s.split(".")
    ip = int(ip)
    if style == "1,234.56":
        return f"{ip:,}.{dp}"
    if style == "1.234,56":
        return f"{ip:,}".replace(",", ".") + "," + dp
    if style == "1 234,56":
        return f"{ip:,}".replace(",", " ") + "," + dp
    return s

def fmt_date(dt, conv):
    if conv == "month-first":
        return dt.strftime("%m/%d/%Y")
    if conv == "day-first":
        return dt.strftime("%d/%m/%Y")
    return dt.strftime("%Y-%m-%d")

def currency_symbol(curr):
    return {"USD": "$", "CAD": "C$", "GBP": "£", "EUR": "€"}[curr]

# ---- Invoice generation ----
def generate_invoice_data(idx, supplier, render_type):
    cc = supplier["cc"]
    info = COUNTRIES[cc]
    # Currency: US always USD; foreign ~55% home, 45% USD
    if cc == "US":
        curr = "USD"
    else:
        curr = info["curr"] if rng.random() < 0.55 else "USD"
    # Dates
    inv_date = datetime(2025, 1, 1) + timedelta(days=rng.randint(0, 600))
    # Invoice number
    prefixes = {"US": ["INV-", ""], "CA": ["INV-", ""], "GB": ["INV-", "SI-"], "IE": ["INV-", ""],
                "DE": ["RE-", ""], "FR": ["FA-", ""], "NL": ["F-", ""], "IT": ["FT-", ""], "ES": ["F-", ""]}
    pref = rng.choice(prefixes.get(cc, ["INV-"]))
    inv_no = f"{pref}{rng.randint(10000, 99999)}" if pref else str(rng.randint(100000, 999999))
    if rng.random() < 0.3:
        inv_no = f"{pref}{inv_date.year}-{rng.randint(100, 999)}"
    # Items
    n_items = rng.randint(1, 4)
    items = []
    subtotal = 0.0
    for i in range(n_items):
        qty = rng.randint(1, 20)
        price = round(rng.uniform(15, 850), 2)
        line = round(qty * price, 2)
        subtotal += line
        desc = rng.choice([
            "Precision machined bracket", "Software license annual", "Consulting hours",
            "Raw aluminum stock", "Freight charges", "Custom fabrication",
            "Electronic assembly", "Design services", "Shipping and handling"
        ])
        items.append({"desc": desc, "qty": qty, "price": price, "line": line})
    subtotal = round(subtotal, 2)
    # Tax
    tax_rate = 0.0
    if cc == "US":
        tax_rate = rng.choice([0.0, 0.0625, 0.08, 0.0])
    elif cc == "CA":
        tax_rate = rng.choice([0.05, 0.13, 0.15])
    elif cc in ("GB", "IE"):
        tax_rate = 0.20
    elif cc == "DE":
        tax_rate = 0.19
    elif cc == "FR":
        tax_rate = 0.20
    elif cc == "NL":
        tax_rate = 0.21
    elif cc in ("IT", "ES"):
        tax_rate = 0.22
    tax = round(subtotal * tax_rate, 2)
    total = round(subtotal + tax, 2)
    # Sometimes mismatch
    mismatch = False
    if rng.random() < 0.08:  # ~5 mismatches
        total = round(total + rng.choice([-12.50, 7.25, -3.40, 18.00]), 2)
        mismatch = True
    # Terms and due
    terms_options = {
        "US": ["Net 30", "Due on receipt", "Net 45", "2/10 Net 30", "Payment due within 30 days"],
        "CA": ["Net 30", "Due on receipt", "30 days", "Net 60"],
        "GB": ["Net 30", "30 days", "Due on receipt", "Payment terms: 30 days"],
        "IE": ["Net 30", "30 days", "Due on receipt"],
        "DE": ["30 Tage netto", "zahlbar innerhalb 30 Tagen", "sofort zahlbar", "2 % Skonto innerhalb 14 Tagen, 30 Tage netto", "30 Tage zum Monatsende"],
        "FR": ["30 jours net", "à 30 jours", "à réception", "escompte 2 % à 10 jours, net à 30 jours", "30 jours fin de mois"],
        "NL": ["netto 30 dagen", "binnen 30 dagen", "bij ontvangst", "30 dagen einde maand"],
        "IT": ["30 giorni netto", "a 30 giorni", "al ricevimento", "fine mese"],
        "ES": ["30 días neto", "a 30 días", "a la recepción", "fin de mes"],
    }
    terms = rng.choice(terms_options.get(cc, ["Net 30"]))
    # Sometimes print due date explicitly
    print_due = rng.random() < 0.35
    due_date_obj = None
    due_source = None
    if print_due:
        # mostly after, sometimes before for exception
        delta = rng.choice([15, 30, 45, -5, 60])
        due_date_obj = inv_date + timedelta(days=delta)
        due_source = "printed"
    else:
        # parse terms
        due_iso, src = parse_due_from_terms(inv_date, terms)
        if due_iso:
            due_date_obj = datetime.strptime(due_iso, "%Y-%m-%d")
            due_source = src
    # Sometimes omit fields for missing_
    omit_due = False
    omit_invno = False
    omit_tax = False
    if rng.random() < 0.06:
        omit_due = True
        due_date_obj = None
        due_source = None
        terms = None
    if rng.random() < 0.04:
        omit_invno = True
        inv_no = None
    if rng.random() < 0.05 and tax == 0:
        omit_tax = True
    # Exceptions
    reasons = []
    if curr != "USD":
        reasons.append("foreign_currency")
    if due_date_obj is None:
        reasons.append("missing_due_date")
    if inv_no is None:
        reasons.append("missing_invoice_number")
    if omit_tax:
        reasons.append("missing_tax")
    if due_date_obj and due_date_obj < inv_date:
        reasons.append("due_before_invoice")
    if mismatch:
        reasons.append("total_mismatch")
    exception = len(reasons) > 0
    # Label values
    label = {
        "supplier": supplier["name"],
        "invoice_number": inv_no,
        "invoice_date": inv_date.strftime("%Y-%m-%d"),
        "due_date": due_date_obj.strftime("%Y-%m-%d") if due_date_obj else None,
        "subtotal": f"{subtotal:.2f}",
        "tax": f"{tax:.2f}" if not omit_tax else None,
        "total": f"{total:.2f}",
        "currency": curr,
        "exception": exception,
        "exception_reasons": reasons,
        "terms": terms,
        "due_source": due_source,
        "country": cc,
        "date_convention": info["date"],
        "number_style": info["num"],
        "layout": supplier["layout"],
        "render": render_type,
    }
    data = {
        "supplier": supplier,
        "buyer": BUYER,
        "inv_no": inv_no,
        "inv_date": inv_date,
        "due_date": due_date_obj,
        "items": items,
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
        "curr": curr,
        "terms": terms,
        "print_due": print_due,
        "omit_tax": omit_tax,
        "info": info,
        "label": label,
    }
    return data

def parse_due_from_terms(inv_date, terms):
    if not terms:
        return None, None
    t = terms.lower()
    receipt = ["due on receipt", "upon receipt", "due immediately", "à réception", "sofort zahlbar", "bij ontvangst", "al ricevimento", "a la recepción"]
    if any(p in t for p in receipt):
        return inv_date.strftime("%Y-%m-%d"), "terms"
    # days
    m = re.search(r'(\d+)\s*(days|jours|tage|giorni|días|dagen)', t)
    if m:
        days = int(m.group(1))
        if not (1 <= days <= 180):
            return None, None
        # EOM check
        eom = ["eom", "end of month", "fin de mois", "monatsende", "fine mese", "einde maand", "fin de mes", "zum monatsende"]
        if any(e in t for e in eom):
            return None, None
        # discount
        if any(x in t for x in ["skonto", "escompte", "2/10", "2 %"]):
            netm = re.search(r'(?:net|netto|net à)\s*(\d+)', t)
            if netm:
                nd = int(netm.group(1))
                if 1 <= nd <= 180:
                    return (inv_date + timedelta(days=nd)).strftime("%Y-%m-%d"), "terms"
            return None, None
        return (inv_date + timedelta(days=days)).strftime("%Y-%m-%d"), "terms"
    return None, None

# ---- Rendering ----
def draw_header(c, data, y_start, style="standard"):
    s = data["supplier"]
    info = data["info"]
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, y_start, s["name"])
    c.setFont("Helvetica", 9)
    c.drawString(40, y_start - 16, f"{s.get('city', '')}, {s.get('state', s.get('province', ''))}")
    c.drawString(40, y_start - 28, f"Tax ID: {s.get('tax_id', 'XX-XXXXXXX')}")
    c.drawString(40, y_start - 40, f"Tel: {s.get('phone', '+1 555 0100')}")
    # Currency indicator
    c.setFont("Helvetica-Bold", 11)
    sym = currency_symbol(data["curr"])
    c.drawRightString(550, y_start, f"Currency: {data['curr']} ({sym})")
    return y_start - 60

def draw_invoice(c, data, pagesize):
    width, height = pagesize
    c.setPageSize(pagesize)
    layout = data["supplier"]["layout"]
    # Layout-specific backgrounds and accents
    if "modern" in layout:
        c.setFillColor(HexColor("#f0f4f8"))
        c.rect(0, 0, width, height, fill=1, stroke=0)
        c.setFillColor(HexColor("#1a365d"))
        c.rect(0, height-8, width, 8, fill=1, stroke=0)
        c.setFillColor(black)
    elif "classic" in layout:
        c.setStrokeColor(HexColor("#333333"))
        c.setLineWidth(1.5)
        c.rect(20, 20, width-40, height-40, stroke=1, fill=0)
    elif "eu_" in layout or "fr_" in layout or "it_" in layout or "es_" in layout or "nl_" in layout:
        c.setFillColor(HexColor("#fafafa"))
        c.rect(0, 0, width, height, fill=1, stroke=0)
        c.setFillColor(black)
    y = height - 50
    # Bilingual note for CA
    if data["supplier"]["cc"] == "CA" and "bilingual" in layout:
        c.setFont("Helvetica", 8)
        c.drawString(40, height - 30, "Facture / Invoice")
    y = draw_header(c, data, y)
    # Invoice meta
    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, "INVOICE")
    y -= 20
    c.setFont("Helvetica", 10)
    if data["inv_no"]:
        c.drawString(40, y, f"Invoice Number: {data['inv_no']}")
    y -= 14
    conv = data["info"]["date"]
    c.drawString(40, y, f"Invoice Date: {fmt_date(data['inv_date'], conv)}")
    y -= 14
    if data["print_due"] and data["due_date"]:
        c.drawString(40, y, f"Due Date: {fmt_date(data['due_date'], conv)}")
        y -= 14
    if data["terms"]:
        c.drawString(40, y, f"Payment Terms: {data['terms']}")
        y -= 18
    # Bill to
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, y, "Bill To:")
    c.setFont("Helvetica", 9)
    c.drawString(40, y - 12, data["buyer"]["name"])
    c.drawString(40, y - 24, data["buyer"]["addr"])
    c.drawString(40, y - 36, data["buyer"]["city"])
    y -= 60
    # Table header
    c.setFont("Helvetica-Bold", 9)
    c.drawString(40, y, "Description")
    c.drawString(300, y, "Qty")
    c.drawString(360, y, "Unit Price")
    c.drawString(460, y, "Amount")
    y -= 4
    c.line(40, y, 540, y)
    y -= 14
    c.setFont("Helvetica", 9)
    style = data["info"]["num"]
    for it in data["items"]:
        c.drawString(40, y, it["desc"][:40])
        c.drawString(300, y, str(it["qty"]))
        c.drawString(360, y, fmt_num(it["price"], style))
        c.drawRightString(540, y, fmt_num(it["line"], style))
        y -= 14
    y -= 10
    # Totals
    c.setFont("Helvetica", 10)
    c.drawRightString(450, y, "Subtotal:")
    c.drawRightString(540, y, fmt_num(data["subtotal"], style))
    y -= 14
    if not data["omit_tax"]:
        tax_label = data["info"]["tax"]
        c.drawRightString(450, y, f"{tax_label}:")
        c.drawRightString(540, y, fmt_num(data["tax"], style))
        y -= 14
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(450, y, "Total:")
    c.drawRightString(540, y, f"{currency_symbol(data['curr'])} {fmt_num(data['total'], style)}")
    y -= 30
    # Bank / notes
    c.setFont("Helvetica", 8)
    c.drawString(40, y, "Bank details (fictional): IBAN XX00 0000 0000 0000 0000 00")
    y -= 12
    c.drawString(40, y, "Please quote invoice number on payment.")
    # Footer
    c.setFont("Helvetica", 7)
    c.drawString(40, 30, f"Page 1 of 1 | {data['supplier']['name']} | {data['curr']}")

def render_pdf(path, data, pagesize):
    c = canvas.Canvas(path, pagesize=pagesize)
    draw_invoice(c, data, pagesize)
    c.save()

# ---- Degradation ----
def degrade_to_scan(src_pdf, dst_pdf, dpi=200):
    images = convert_from_path(src_pdf, dpi=dpi)
    img = images[0].convert("RGB")
    # slight rotation
    angle = rng.uniform(-1.8, 1.8)
    img = img.rotate(angle, expand=True, fillcolor=(255, 255, 255))
    # noise
    arr = np.array(img).astype(np.int16)
    noise = np.random.normal(0, 8, arr.shape).astype(np.int16)
    arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr)
    # mild blur
    img = img.filter(ImageFilter.GaussianBlur(radius=0.6))
    # slight contrast
    img = ImageEnhance.Contrast(img).enhance(0.95)
    # save as PDF
    img.save(dst_pdf, "PDF", resolution=dpi)

def degrade_to_photo(src_pdf, dst_pdf, dpi=220):
    images = convert_from_path(src_pdf, dpi=dpi)
    img = images[0].convert("RGB")
    # more rotation
    angle = rng.uniform(-3.5, 3.5)
    img = img.rotate(angle, expand=True, fillcolor=(248, 248, 245))
    # uneven lighting
    arr = np.array(img).astype(np.float32)
    h, w = arr.shape[:2]
    y_grad = np.linspace(0.85, 1.08, h)[:, None]
    x_grad = np.linspace(0.92, 1.05, w)[None, :]
    light = (y_grad * x_grad)[..., None]
    arr = np.clip(arr * light, 0, 255)
    img = Image.fromarray(arr.astype(np.uint8))
    # blur
    img = img.filter(ImageFilter.GaussianBlur(radius=1.2))
    # vignette-ish
    img = ImageEnhance.Brightness(img).enhance(0.97)
    img = ImageEnhance.Contrast(img).enhance(1.05)
    img.save(dst_pdf, "PDF", resolution=dpi)

# ---- Main ----
def main():
    invoices = []
    # Better balance: assign target counts per country then distribute renders
    # Target ~12 US, rest spread
    country_targets = {"US": 12, "CA": 6, "GB": 6, "IE": 6, "DE": 6, "FR": 6, "NL": 6, "IT": 6, "ES": 6}
    # Build supplier pools
    from collections import defaultdict
    pools = defaultdict(list)
    for s in SUPPLIERS:
        pools[s["cc"]].append(s)
    # Generate list of (supplier, render) pairs
    assignments = []
    for cc, target in country_targets.items():
        suppliers = pools[cc]
        # renders: half clean, quarter scan, quarter photo, at least one of each if target >= 3
        n_clean = max(1, target // 2)
        n_scan = max(1, target // 4)
        n_photo = target - n_clean - n_scan
        if n_photo < 1:
            n_photo = 1
            n_clean = target - n_scan - n_photo
        renders_for_cc = ["clean"] * n_clean + ["scan"] * n_scan + ["photo"] * n_photo
        rng.shuffle(renders_for_cc)
        for r in renders_for_cc:
            assignments.append((rng.choice(suppliers), r))
    rng.shuffle(assignments)
    # Generate
    for i, (supplier, render) in enumerate(assignments):
        data = generate_invoice_data(i + 1, supplier, render)
        slug = re.sub(r'[^a-z0-9]+', '-', supplier["name"].lower())[:30].strip("-")
        fid = f"{i+1:03d}-{slug}"
        folder = {"clean": "clean", "scan": "scans", "photo": "photos"}[render]
        pdf_path = os.path.join(OUT, folder, f"{fid}.pdf")
        pagesize = data["info"]["ps"]
        # Always render clean first to temp if needed
        if render == "clean":
            render_pdf(pdf_path, data, pagesize)
        else:
            tmp = f"/tmp/invoice_gen/tmp_{fid}.pdf"
            render_pdf(tmp, data, pagesize)
            if render == "scan":
                degrade_to_scan(tmp, pdf_path)
            else:
                degrade_to_photo(tmp, pdf_path)
            os.remove(tmp)
        # sha256
        with open(pdf_path, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        label = data["label"]
        label["id"] = fid
        label["file"] = f"holdout-3/{folder}/{fid}.pdf"
        label["sha256"] = sha
        invoices.append(label)
        print(f"Generated {fid} ({render}, {supplier['cc']}, {data['curr']})")
    # labels.json
    out_json = {
        "fields": ["supplier", "invoice_number", "invoice_date", "due_date", "subtotal", "tax", "total"],
        "frozen": None,
        "generator": "make_set.py",
        "seed": SEED,
        "layouts": LAYOUTS,
        "invoices": invoices,
    }
    with open(os.path.join(OUT, "labels.json"), "w") as f:
        json.dump(out_json, f, indent=2)
    print(f"\nDone. {len(invoices)} invoices written.")
    # Summary
    from collections import Counter
    print("Renders:", Counter(l["render"] for l in invoices))
    print("Countries:", Counter(l["country"] for l in invoices))
    print("Currencies:", Counter(l["currency"] for l in invoices))
    all_reasons = []
    for l in invoices:
        all_reasons.extend(l["exception_reasons"])
    print("Exception reasons:", Counter(all_reasons))

if __name__ == "__main__":
    main()
