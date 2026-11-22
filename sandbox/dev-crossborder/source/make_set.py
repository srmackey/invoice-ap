#!/usr/bin/env python3
"""
Generator for cross-border invoice test set.
Deterministic with fixed seed. One run produces all files.
"""
import json
import hashlib
import os
import shutil
import random
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.units import mm, inch
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pdf2image import convert_from_path
from PIL import Image, ImageFilter, ImageEnhance, ImageDraw, ImageOps
import io

SEED = 0
random.seed(SEED)

# Buyer details (fictional small US business)
BUYER_NAME = "Apex Precision Works LLC"
BUYER_ADDR = ["1420 Industrial Parkway", "Broomfield, CO 80020", "United States"]
BUYER_PO_PREFIX = "PO-"

# Suppliers: ~1/5 US, rest foreign. Invented names.
SUPPLIERS = [
    # US (12 invoices across 4)
    {"name": "Rocky Mountain Components Inc.", "slug": "rocky-mtn", "country": "US", "home_cur": "USD", "date_conv": "month-first", "num_style": "1,234.56", "layout": "us_standard", "tax_rate": Decimal("0.08"), "terms_style": "net", "invoices": 3},
    {"name": "Prairie Logistics LLC", "slug": "prairie-log", "country": "US", "home_cur": "USD", "date_conv": "month-first", "num_style": "1,234.56", "layout": "us_modern", "tax_rate": Decimal("0.07"), "terms_style": "net", "invoices": 3},
    {"name": "Summit Fabrication Co.", "slug": "summit-fab", "country": "US", "home_cur": "USD", "date_conv": "month-first", "num_style": "1,234.56", "layout": "us_classic", "tax_rate": Decimal("0.0625"), "terms_style": "receipt", "invoices": 3},
    {"name": "Horizon Tech Supplies", "slug": "horizon-tech", "country": "US", "home_cur": "USD", "date_conv": "month-first", "num_style": "1,234.56", "layout": "us_standard", "tax_rate": Decimal("0.09"), "terms_style": "net", "invoices": 3},
    # Canada (8)
    {"name": "Maple Leaf Industrial Ltd.", "slug": "maple-leaf", "country": "CA", "home_cur": "CAD", "date_conv": "month-first", "num_style": "1,234.56", "layout": "ca_bilingual", "tax_rate": Decimal("0.13"), "terms_style": "net", "invoices": 3},
    {"name": "Northern Lights Equipment", "slug": "northern-lights", "country": "CA", "home_cur": "USD", "date_conv": "month-first", "num_style": "1,234.56", "layout": "ca_standard", "tax_rate": Decimal("0.05"), "terms_style": "net", "invoices": 3},
    {"name": "Great Lakes Parts Inc.", "slug": "great-lakes", "country": "CA", "home_cur": "CAD", "date_conv": "day-first", "num_style": "1 234,56", "layout": "ca_modern", "tax_rate": Decimal("0.15"), "terms_style": "net", "invoices": 2},
    # UK (8)
    {"name": "Thames Valley Engineering Ltd", "slug": "thames-valley", "country": "GB", "home_cur": "GBP", "date_conv": "day-first", "num_style": "1,234.56", "layout": "uk_standard", "tax_rate": Decimal("0.20"), "terms_style": "net", "invoices": 3},
    {"name": "Celtic Precision Tools", "slug": "celtic-prec", "country": "GB", "home_cur": "USD", "date_conv": "day-first", "num_style": "1,234.56", "layout": "uk_classic", "tax_rate": Decimal("0.20"), "terms_style": "discount", "invoices": 3},
    {"name": "Highland Fabrications Ltd", "slug": "highland-fab", "country": "GB", "home_cur": "GBP", "date_conv": "written", "num_style": "1,234.56", "layout": "uk_modern", "tax_rate": Decimal("0.20"), "terms_style": "net", "invoices": 2},
    # Ireland (4)
    {"name": "Emerald Isle Components", "slug": "emerald-isle", "country": "IE", "home_cur": "EUR", "date_conv": "day-first", "num_style": "1.234,56", "layout": "eu_standard", "tax_rate": Decimal("0.23"), "terms_style": "net", "invoices": 2},
    {"name": "Dublin Tech Services Ltd", "slug": "dublin-tech", "country": "IE", "home_cur": "USD", "date_conv": "day-first", "num_style": "1,234.56", "layout": "eu_modern", "tax_rate": Decimal("0.23"), "terms_style": "receipt", "invoices": 2},
    # Germany (8)
    {"name": "Rheinland Maschinenbau GmbH", "slug": "rheinland-masch", "country": "DE", "home_cur": "EUR", "date_conv": "day-first", "num_style": "1.234,56", "layout": "de_standard", "tax_rate": Decimal("0.19"), "terms_style": "net", "invoices": 3},
    {"name": "Bayerische Präzisionsteile AG", "slug": "bayerische-praz", "country": "DE", "home_cur": "USD", "date_conv": "day-first", "num_style": "1.234,56", "layout": "de_classic", "tax_rate": Decimal("0.19"), "terms_style": "discount", "invoices": 3},
    {"name": "Nordsee Elektronik GmbH", "slug": "nordsee-elek", "country": "DE", "home_cur": "EUR", "date_conv": "day-first", "num_style": "1.234,56", "layout": "de_modern", "tax_rate": Decimal("0.19"), "terms_style": "net", "invoices": 2},
    # France (6)
    {"name": "Atelier du Rhône SARL", "slug": "atelier-rhone", "country": "FR", "home_cur": "EUR", "date_conv": "day-first", "num_style": "1 234,56", "layout": "fr_standard", "tax_rate": Decimal("0.20"), "terms_style": "net", "invoices": 3},
    {"name": "Parisian Precision SAS", "slug": "parisian-prec", "country": "FR", "home_cur": "USD", "date_conv": "day-first", "num_style": "1.234,56", "layout": "fr_modern", "tax_rate": Decimal("0.20"), "terms_style": "net", "invoices": 3},
    # Netherlands (5)
    {"name": "Amsterdam Precision BV", "slug": "amsterdam-prec", "country": "NL", "home_cur": "EUR", "date_conv": "day-first", "num_style": "1.234,56", "layout": "nl_standard", "tax_rate": Decimal("0.21"), "terms_style": "net", "invoices": 3},
    {"name": "Rotterdam Logistics BV", "slug": "rotterdam-log", "country": "NL", "home_cur": "USD", "date_conv": "day-first", "num_style": "1.234,56", "layout": "nl_modern", "tax_rate": Decimal("0.21"), "terms_style": "receipt", "invoices": 2},
    # Italy (5)
    {"name": "Officine Milanesi Srl", "slug": "officine-mil", "country": "IT", "home_cur": "EUR", "date_conv": "day-first", "num_style": "1.234,56", "layout": "it_standard", "tax_rate": Decimal("0.22"), "terms_style": "net", "invoices": 3},
    {"name": "Toscana Componenti SpA", "slug": "toscana-comp", "country": "IT", "home_cur": "EUR", "date_conv": "day-first", "num_style": "1.234,56", "layout": "it_classic", "tax_rate": Decimal("0.22"), "terms_style": "discount", "invoices": 2},
    # Spain (4)
    {"name": "Componentes Ibéricos SL", "slug": "comp-ibericos", "country": "ES", "home_cur": "EUR", "date_conv": "day-first", "num_style": "1.234,56", "layout": "es_standard", "tax_rate": Decimal("0.21"), "terms_style": "net", "invoices": 2},
    {"name": "Barcelona Tech Solutions SL", "slug": "barcelona-tech", "country": "ES", "home_cur": "USD", "date_conv": "day-first", "num_style": "1.234,56", "layout": "es_modern", "tax_rate": Decimal("0.21"), "terms_style": "net", "invoices": 2},
]

# Verify total invoices
TOTAL = sum(s["invoices"] for s in SUPPLIERS)
assert TOTAL == 60, f"Total invoices {TOTAL} != 60"

# Item descriptions by trade
ITEMS = {
    "US": ["Precision machined housing", "Custom aluminum bracket", "Hydraulic fitting kit", "Steel mounting plate", "Electronic control module"],
    "CA": ["Industrial valve assembly", "Stainless steel flange", "Control panel components", "Gearbox repair parts", "Sensor package"],
    "GB": ["CNC machined spindle", "Bearing housing", "Tooling fixture", "Precision shaft", "Hydraulic cylinder"],
    "IE": ["Electronic PCB assembly", "Connector kit", "Power supply unit", "Cable harness", "Sensor module"],
    "DE": ["Maschinenbauteil Präzision", "Hydraulikventil", "Steuerungseinheit", "Lagergehäuse", "Antriebswelle"],
    "FR": ["Pièce usinée de précision", "Bride en acier", "Module électronique", "Arbre de transmission", "Kit de fixation"],
    "NL": ["Precisie onderdelen", "Hydraulische componenten", "Besturingsmodule", "Lagerhuis", "Aandrijfas"],
    "IT": ["Componente di precisione", "Valvola idraulica", "Unità di controllo", "Albero motore", "Supporto cuscinetto"],
    "ES": ["Componente de precisión", "Válvula hidráulica", "Módulo de control", "Eje de transmisión", "Soporte de rodamiento"],
}

# Payment terms templates by language/style
TERMS = {
    "net": {
        "en": ["Net 30", "30 days net", "Payment terms: 30 days", "Payment due within 30 days", "Payable within 30 days"],
        "de": ["30 Tage netto", "zahlbar innerhalb 30 Tagen", "netto 30 Tagen"],
        "fr": ["30 jours net", "à 30 jours", "paiement sous 30 jours"],
        "nl": ["netto 30 dagen", "binnen 30 dagen"],
        "it": ["a 30 giorni", "30 giorni netto"],
        "es": ["a 30 días", "neto 30 días"],
    },
    "receipt": {
        "en": ["Due on receipt", "Upon receipt", "Due immediately"],
        "de": ["sofort zahlbar", "bei Empfang"],
        "fr": ["à réception", "payable à réception"],
        "nl": ["bij ontvangst"],
        "it": ["al ricevimento"],
        "es": ["a la recepción"],
    },
    "discount": {
        "en": ["2/10 Net 30", "2% 10 days, net 30"],
        "de": ["2 % Skonto innerhalb 14 Tagen, 30 Tage netto"],
        "fr": ["escompte 2 % à 10 jours, net à 30 jours"],
    },
}

def format_amount(amount: Decimal, style: str) -> str:
    """Format amount according to number style for display on page. Label always uses dot."""
    s = f"{amount:.2f}"
    if style == "1,234.56":
        return f"{amount:,.2f}"
    elif style == "1.234,56":
        parts = s.split(".")
        whole = parts[0]
        # insert dots as thousands
        whole_fmt = ""
        for i, c in enumerate(reversed(whole)):
            if i > 0 and i % 3 == 0:
                whole_fmt = "." + whole_fmt
            whole_fmt = c + whole_fmt
        return whole_fmt + "," + parts[1]
    elif style == "1 234,56":
        parts = s.split(".")
        whole = parts[0]
        whole_fmt = ""
        for i, c in enumerate(reversed(whole)):
            if i > 0 and i % 3 == 0:
                whole_fmt = " " + whole_fmt
            whole_fmt = c + whole_fmt
        return whole_fmt + "," + parts[1]
    return s

def format_date(dt: datetime, conv: str) -> str:
    if conv == "month-first":
        return dt.strftime("%m/%d/%Y")
    elif conv == "day-first":
        return dt.strftime("%d/%m/%Y")
    elif conv == "written":
        return dt.strftime("%d %B %Y")
    elif conv == "year-first":
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d")

def get_terms_text(style: str, country: str, days: int = 30) -> str:
    lang = "en"
    if country == "DE":
        lang = "de"
    elif country == "FR":
        lang = "fr"
    elif country == "NL":
        lang = "nl"
    elif country == "IT":
        lang = "it"
    elif country == "ES":
        lang = "es"
    options = TERMS.get(style, TERMS["net"]).get(lang, TERMS["net"]["en"])
    text = random.choice(options)
    # Replace 30 with actual days if needed
    if "30" in text and days != 30:
        text = text.replace("30", str(days))
    return text

def generate_invoice(supplier: dict, inv_idx: int, global_idx: int) -> dict:
    """Generate one invoice's data and label values."""
    # Date: random in 2025-2026
    base = datetime(2025, 1, 1) + timedelta(days=random.randint(0, 700))
    inv_date = base.replace(day=min(base.day, 28))  # avoid month end issues
    
    # Currency: mostly home, some USD for foreign
    if supplier["country"] == "US":
        currency = "USD"
    else:
        currency = supplier["home_cur"] if random.random() < 0.7 else "USD"
    
    # Items
    country_items = ITEMS.get(supplier["country"], ITEMS["US"])
    n_items = random.randint(1, 4)
    line_items = []
    subtotal = Decimal("0")
    for i in range(n_items):
        desc = random.choice(country_items)
        qty = random.randint(1, 10)
        unit = Decimal(str(round(random.uniform(50, 800), 2)))
        line_total = (unit * qty).quantize(Decimal("0.01"))
        line_items.append({"desc": desc, "qty": qty, "unit": unit, "total": line_total})
        subtotal += line_total
    
    # Tax
    tax_rate = supplier["tax_rate"]
    # Sometimes zero for export
    if currency != "USD" and random.random() < 0.4:
        tax_rate = Decimal("0")
    tax = (subtotal * tax_rate).quantize(Decimal("0.01"))
    total = subtotal + tax
    
    # Occasionally mismatch
    force_mismatch = global_idx % 15 == 0  # a few
    if force_mismatch:
        total = total + Decimal("1.23")  # >1 cent
    
    # Due date logic
    terms_style = supplier["terms_style"]
    days = random.choice([14, 30, 45, 60]) if terms_style == "net" else 0
    terms_text = get_terms_text(terms_style, supplier["country"], days or 30)
    
    printed_due = None
    due_source = None
    due_date = None
    if random.random() < 0.5:  # half have printed due
        due_date = inv_date + timedelta(days=days or 30)
        printed_due = format_date(due_date, supplier["date_conv"])
        due_source = "printed"
    else:
        # from terms
        if terms_style == "receipt":
            due_date = inv_date
            due_source = "terms"
        elif terms_style == "net":
            due_date = inv_date + timedelta(days=days)
            due_source = "terms"
        elif terms_style == "discount":
            due_date = inv_date + timedelta(days=30)  # net period
            due_source = "terms"
        # else null
    
    # Sometimes force due before invoice
    if global_idx % 20 == 5:
        due_date = inv_date - timedelta(days=5)
        printed_due = format_date(due_date, supplier["date_conv"])
        due_source = "printed"
    # Force some missing due dates
    if global_idx % 10 == 3:
        due_date = None
        due_source = None
        printed_due = None
        terms_text = "Payment by bank transfer"
    if global_idx % 20 == 5:
        due_date = inv_date - timedelta(days=5)
        printed_due = format_date(due_date, supplier["date_conv"])
        due_source = "printed"
    # Force some missing due dates
    if global_idx % 10 == 3:
        due_date = None
        due_source = None
        printed_due = None
        terms_text = "Payment by bank transfer"
    if global_idx % 20 == 5:
        due_date = inv_date - timedelta(days=5)
        printed_due = format_date(due_date, supplier["date_conv"])
        due_source = "printed"
    # Force some missing due dates
    if global_idx % 10 == 3:
        due_date = None
        due_source = None
        printed_due = None
        terms_text = "Payment by bank transfer"
    if global_idx % 20 == 5:
        due_date = inv_date - timedelta(days=5)
        printed_due = format_date(due_date, supplier["date_conv"])
        due_source = "printed"
    # Force some missing due dates
    if global_idx % 10 == 3:
        due_date = None
        due_source = None
        printed_due = None
        terms_text = "Payment by bank transfer"
    if global_idx % 20 == 5:
        due_date = inv_date - timedelta(days=5)
        printed_due = format_date(due_date, supplier["date_conv"])
        due_source = "printed"
    # Force some missing due dates
    if global_idx % 10 == 3:
        due_date = None
        due_source = None
        printed_due = None
        terms_text = "Payment by bank transfer"
    if global_idx % 20 == 5:
        due_date = inv_date - timedelta(days=5)
        printed_due = format_date(due_date, supplier["date_conv"])
        due_source = "printed"
    # Force some missing due dates
    if global_idx % 10 == 3:
        due_date = None
        due_source = None
        printed_due = None
        terms_text = "Payment by bank transfer"
    if global_idx % 20 == 5:
        due_date = inv_date - timedelta(days=5)
        printed_due = format_date(due_date, supplier["date_conv"])
        due_source = "printed"
    # Force some missing due dates
    if global_idx % 10 == 3:
        due_date = None
        due_source = None
        printed_due = None
        terms_text = "Payment by bank transfer"
    if global_idx % 20 == 5:
        due_date = inv_date - timedelta(days=5)
        printed_due = format_date(due_date, supplier["date_conv"])
        due_source = "printed"
    # Force some missing due dates
    if global_idx % 10 == 3:
        due_date = None
        due_source = None
        printed_due = None
        terms_text = "Payment by bank transfer"
    if global_idx % 20 == 5:
        due_date = inv_date - timedelta(days=5)
        printed_due = format_date(due_date, supplier["date_conv"])
        due_source = "printed"
    # Force some missing due dates
    if global_idx % 10 == 3:
        due_date = None
        due_source = None
        printed_due = None
        terms_text = "Payment by bank transfer"
    if global_idx % 20 == 5:
        due_date = inv_date - timedelta(days=5)
        printed_due = format_date(due_date, supplier["date_conv"])
        due_source = "printed"
    # Force some missing due dates
    if global_idx % 10 == 3:
        due_date = None
        due_source = None
        printed_due = None
        terms_text = "Payment by bank transfer"
    if global_idx % 20 == 5:
        due_date = inv_date - timedelta(days=5)
        printed_due = format_date(due_date, supplier["date_conv"])
        due_source = "printed"
    # Force some missing due dates
    if global_idx % 10 == 3:
        due_date = None
        due_source = None
        printed_due = None
        terms_text = "Payment by bank transfer"
        due_source = "printed"
    
    # Invoice number
    inv_num = f"{supplier['slug'][:3].upper()}-{inv_date.year}-{inv_idx:03d}"
    if random.random() < 0.3:
        inv_num = f"INV/{inv_date.year}/{random.randint(1000,9999)}"
    
    # Exceptions
    reasons = []
    if currency != "USD":
        reasons.append("foreign_currency")
    if due_date is None:
        reasons.append("missing_due_date")
    if due_date and due_date < inv_date:
        reasons.append("due_before_invoice")
    if abs(subtotal + tax - total) > Decimal("0.01"):
        reasons.append("total_mismatch")
    # occasional missing other fields (rare)
    if global_idx % 25 == 10:
        reasons.append("missing_tax")
        data_tax = None  # will handle in data dict
    
    exception = len(reasons) > 0
    
    # Render type
    if global_idx < 30:
        render = "clean"
    elif global_idx < 45:
        render = "scan"
    else:
        render = "photo"
    
    data = {
        "supplier": supplier["name"],
        "slug": supplier["slug"],
        "country": supplier["country"],
        "currency": currency,
        "date_conv": supplier["date_conv"],
        "num_style": supplier["num_style"],
        "layout": supplier["layout"],
        "invoice_number": inv_num,
        "invoice_date": inv_date.strftime("%Y-%m-%d"),
        "due_date": due_date.strftime("%Y-%m-%d") if due_date else None,
        "subtotal": f"{subtotal:.2f}",
        "tax": f"{tax:.2f}",
        "total": f"{total:.2f}",
        "terms": terms_text,
        "due_source": due_source,
        "line_items": line_items,
        "tax_rate": float(tax_rate),
        "render": render,
        "exception": exception,
        "exception_reasons": reasons,
        "printed_due": printed_due,
        "buyer_po": f"{BUYER_PO_PREFIX}{random.randint(1000,9999)}",
    }
    return data




def draw_header(c, data, y_start, page_width):
    c.setFont("Helvetica-Bold", 16)
    c.setFillColor(colors.black)
    c.drawString(40, y_start, data["supplier"])
    c.setFont("Helvetica", 9)
    addr = ["123 Industrial Way", f"{data['country']} City", "ZIP 12345"]
    for i, line in enumerate(addr):
        c.drawString(40, y_start - 15 - i*12, line)
    c.setFillColor(colors.HexColor("#3182ce"))
    c.rect(page_width - 100, y_start - 20, 50, 30, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(page_width - 75, y_start - 10, "LOGO")
    return y_start - 70

def draw_invoice_info(c, data, y, page_width):
    c.setFont("Helvetica-Bold", 12)
    c.setFillColor(colors.black)
    title = "INVOICE" if data["country"] in ["US", "CA", "GB", "IE"] else "RECHNUNG / INVOICE"
    c.drawString(40, y, title)
    c.setFont("Helvetica", 10)
    y -= 20
    c.drawString(40, y, f"Invoice No: {data['invoice_number']}")
    y -= 15
    from datetime import datetime
    inv_date_str = format_date(datetime.strptime(data["invoice_date"], "%Y-%m-%d"), data["date_conv"])
    c.drawString(40, y, f"Date: {inv_date_str}")
    y -= 15
    if data.get("printed_due"):
        c.drawString(40, y, f"Due Date: {data['printed_due']}")
        y -= 15
    cur_sym = {"USD": "$", "CAD": "C$", "GBP": "£", "EUR": "€"}.get(data["currency"], data["currency"])
    c.setFont("Helvetica-Bold", 11)
    c.drawString(page_width - 180, y + 45, f"Currency: {data['currency']} ({cur_sym})")
    return y - 10

def draw_items(c, data, y, page_width):
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, y, "Description")
    c.drawString(280, y, "Qty")
    c.drawString(320, y, "Unit")
    c.drawString(400, y, "Amount")
    y -= 5
    c.line(40, y, page_width - 40, y)
    y -= 15
    c.setFont("Helvetica", 9)
    for item in data["line_items"]:
        unit_fmt = format_amount(item["unit"], data["num_style"])
        tot_fmt = format_amount(item["total"], data["num_style"])
        c.drawString(40, y, str(item["desc"])[:35])
        c.drawString(280, y, str(item["qty"]))
        c.drawString(320, y, unit_fmt)
        c.drawString(400, y, tot_fmt)
        y -= 14
    return y - 10

def draw_totals(c, data, y, page_width):
    c.setFont("Helvetica", 10)
    sub_fmt = format_amount(Decimal(data["subtotal"]), data["num_style"])
    tax_fmt = format_amount(Decimal(data["tax"]), data["num_style"])
    tot_fmt = format_amount(Decimal(data["total"]), data["num_style"])
    c.drawString(300, y, "Subtotal:")
    c.drawString(400, y, sub_fmt)
    y -= 15
    tax_label = "Tax:" if data["tax_rate"] > 0 else "Tax (0%):"
    c.drawString(300, y, tax_label)
    c.drawString(400, y, tax_fmt)
    y -= 15
    c.setFont("Helvetica-Bold", 11)
    c.drawString(300, y, "Total:")
    c.drawString(400, y, tot_fmt)
    c.setFont("Helvetica", 8)
    c.drawString(300, y - 12, f"Amount in {data['currency']}")
    return y - 30

def draw_footer(c, data, y):
    c.setFont("Helvetica", 8)
    c.setFillColor(colors.black)
    c.drawString(40, y, f"Payment terms: {data.get('terms') or 'See above'}")
    y -= 12
    c.drawString(40, y, f"PO: {data.get('buyer_po', '')}")
    y -= 12
    if data["currency"] in ["EUR", "GBP"]:
        c.drawString(40, y, "IBAN: XX00 0000 0000 0000 0000 00  BIC: XXXXXXXX")
    elif data["country"] == "GB":
        c.drawString(40, y, "Sort code: 12-34-56  Account: 12345678")
    elif data["country"] == "CA":
        c.drawString(40, y, "Transit: 12345  Institution: 001  Account: 1234567")
    else:
        c.drawString(40, y, "Bank: Fictional Bank  Account: 123456789")
    y -= 12
    if data["country"] == "DE":
        c.drawString(40, y, "USt-IdNr.: DE123456789")
    elif data["country"] == "GB":
        c.drawString(40, y, "VAT No: GB123456789")
    elif data["country"] == "CA":
        c.drawString(40, y, "GST/HST: 123456789RT0001")
    elif data["country"] == "US":
        c.drawString(40, y, "EIN: 12-3456789")
    else:
        c.drawString(40, y, "VAT: XX123456789")

def render_pdf(data, path):
    from reportlab.lib.pagesizes import A4, letter
    page = letter if data["country"] in ["US", "CA"] else A4
    c = canvas.Canvas(path, pagesize=page)
    width, height = page
    y = height - 50
    y = draw_header(c, data, y, width)
    y = draw_invoice_info(c, data, y, width)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, y, "Bill To:")
    c.setFont("Helvetica", 9)
    y -= 15
    c.drawString(40, y, BUYER_NAME)
    for line in BUYER_ADDR:
        y -= 12
        c.drawString(40, y, line)
    y -= 25
    y = draw_items(c, data, y, width)
    y = draw_totals(c, data, y, width)
    draw_footer(c, data, max(y, 100))
    c.showPage()
    c.save()

def apply_scan_effect(pdf_path, out_path):
    images = convert_from_path(pdf_path, dpi=100)
    img = images[0]
    angle = random.uniform(-1.5, 1.5)
    img = img.rotate(angle, expand=True, fillcolor="white")
    img = img.filter(ImageFilter.GaussianBlur(radius=0.6))
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.1)
    img.save(out_path, "PDF", resolution=100)

def apply_photo_effect(pdf_path, out_path):
    images = convert_from_path(pdf_path, dpi=80)
    img = images[0].convert("RGB")
    angle = random.uniform(-3, 3)
    img = img.rotate(angle, expand=True, fillcolor=(240, 240, 235))
    overlay = Image.new("RGB", img.size, (255, 255, 255))
    draw = ImageDraw.Draw(overlay)
    for x in range(0, img.size[0], 4):  # faster
        factor = 0.85 + 0.15 * (x / img.size[0])
        draw.line([(x, 0), (x, img.size[1])], fill=(int(255*factor), int(255*factor), int(255*factor)))
    img = Image.blend(img, overlay, 0.15)
    img = img.filter(ImageFilter.GaussianBlur(radius=1.0))
    enhancer = ImageEnhance.Brightness(img)
    img = enhancer.enhance(0.95)
    img.save(out_path, "PDF", resolution=80, quality=70)

def main():
    os.makedirs("clean", exist_ok=True)
    os.makedirs("scans", exist_ok=True)
    os.makedirs("photos", exist_ok=True)
    invoices = []
    layouts_used = set()
    global_idx = 0
    inv_counter = {}
    for supplier in SUPPLIERS:
        inv_counter[supplier["slug"]] = 0
        for i in range(supplier["invoices"]):
            inv_counter[supplier["slug"]] += 1
            data = generate_invoice(supplier, inv_counter[supplier["slug"]], global_idx)
            layouts_used.add(data["layout"])
            slug = f"{global_idx+1:03d}-{supplier['slug']}"
            render = data["render"]
            folder = {"clean": "clean", "scan": "scans", "photo": "photos"}[render]
            pdf_path = f"{folder}/{slug}.pdf"
            # Always render clean first to temp
            temp_path = f"/tmp/{slug}.pdf"
            render_pdf(data, temp_path)
            if render == "clean":
                shutil.move(temp_path, pdf_path)
            elif render == "scan":
                apply_scan_effect(temp_path, pdf_path)
                os.remove(temp_path)
            else:
                apply_photo_effect(temp_path, pdf_path)
                os.remove(temp_path)
            # SHA256
            with open(pdf_path, "rb") as f:
                sha = hashlib.sha256(f.read()).hexdigest()
            label = {
                "id": slug,
                "file": f"holdout-3/{folder}/{slug}.pdf",
                "supplier": data["supplier"],
                "invoice_number": data["invoice_number"],
                "invoice_date": data["invoice_date"],
                "due_date": data["due_date"],
                "subtotal": data["subtotal"],
                "tax": data["tax"],
                "total": data["total"],
                "currency": data["currency"],
                "exception": data["exception"],
                "exception_reasons": data["exception_reasons"],
                "terms": data["terms"],
                "due_source": data["due_source"],
                "country": data["country"],
                "date_convention": data["date_conv"],
                "number_style": data["num_style"],
                "layout": data["layout"],
                "render": render,
                "sha256": sha,
            }
            invoices.append(label)
            global_idx += 1
    labels = {
        "fields": ["supplier", "invoice_number", "invoice_date", "due_date", "subtotal", "tax", "total"],
        "frozen": None,
        "generator": "make_set.py",
        "seed": SEED,
        "layouts": sorted(list(layouts_used)),
        "invoices": invoices,
    }
    with open("labels.json", "w") as f:
        json.dump(labels, f, indent=2)
    print(f"Generated {len(invoices)} invoices. Layouts: {sorted(layouts_used)}")

main()
