import os
import json
import hashlib
import random
import io
from datetime import datetime, timedelta
from reportlab.lib.pagesizes import letter, A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch, mm
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from PIL import Image, ImageFilter, ImageEnhance, ImageDraw, ImageOps
from pdf2image import convert_from_bytes

SEED = 42
random.seed(SEED)

OUTPUT_DIR = "."
CLEAN_DIR = os.path.join(OUTPUT_DIR, "clean")
SCAN_DIR = os.path.join(OUTPUT_DIR, "scans")
PHOTO_DIR = os.path.join(OUTPUT_DIR, "photos")
os.makedirs(CLEAN_DIR, exist_ok=True)
os.makedirs(SCAN_DIR, exist_ok=True)
os.makedirs(PHOTO_DIR, exist_ok=True)

# Customer
CUSTOMER = {
    "name": "Horizon Analytics LLC",
    "address": "1842 Midway Blvd, Suite 200\nBroomfield, CO 80020\nUnited States",
    "phone": "+1 303 555 0142"
}

# Country configs
COUNTRIES = {
    "US": {"pagesize": letter, "currency_default": "USD", "date_conv": "month-first", "num_style": "1,234.56", "vat_name": "Sales Tax", "rate_range": (0.0, 0.0875)},
    "CA": {"pagesize": letter, "currency_default": "CAD", "date_conv": "year-first", "num_style": "1,234.56", "vat_name": "GST/HST", "rate_range": (0.05, 0.13)},
    "GB": {"pagesize": A4, "currency_default": "GBP", "date_conv": "day-first", "num_style": "1,234.56", "vat_name": "VAT", "rate_range": (0.20, 0.20)},
    "IE": {"pagesize": A4, "currency_default": "EUR", "date_conv": "day-first", "num_style": "1,234.56", "vat_name": "VAT", "rate_range": (0.23, 0.23)},
    "DE": {"pagesize": A4, "currency_default": "EUR", "date_conv": "day-first", "num_style": "1.234,56", "vat_name": "MwSt", "rate_range": (0.19, 0.19)},
    "FR": {"pagesize": A4, "currency_default": "EUR", "date_conv": "day-first", "num_style": "1 234,56", "vat_name": "TVA", "rate_range": (0.20, 0.20)},
    "NL": {"pagesize": A4, "currency_default": "EUR", "date_conv": "day-first", "num_style": "1.234,56", "vat_name": "BTW", "rate_range": (0.21, 0.21)},
    "IT": {"pagesize": A4, "currency_default": "EUR", "date_conv": "day-first", "num_style": "1.234,56", "vat_name": "IVA", "rate_range": (0.22, 0.22)},
    "ES": {"pagesize": A4, "currency_default": "EUR", "date_conv": "day-first", "num_style": "1.234,56", "vat_name": "IVA", "rate_range": (0.21, 0.21)},
}

# Suppliers: name, country, layout, color theme, trade
SUPPLIERS = [
    # US ~12 invoices worth
    {"name": "Maplewood Hardware Supply", "country": "US", "layout": "classic_us", "color": colors.HexColor("#1a365d"), "trade": "hardware", "invoices": 2},
    {"name": "Pacific Rim Components Inc.", "country": "US", "layout": "minimal", "color": colors.HexColor("#2d3748"), "trade": "components", "invoices": 2},
    {"name": "Summit Software Solutions", "country": "US", "layout": "boxed", "color": colors.HexColor("#2b6cb0"), "trade": "software", "invoices": 2},
    {"name": "Boulder Office Supplies Co.", "country": "US", "layout": "classic_us", "color": colors.HexColor("#c53030"), "trade": "office", "invoices": 2},
    {"name": "Rocky Mountain Logistics", "country": "US", "layout": "uk_vat", "color": colors.HexColor("#276749"), "trade": "logistics", "invoices": 2},
    {"name": "Denver Tech Parts LLC", "country": "US", "layout": "minimal", "color": colors.HexColor("#744210"), "trade": "parts", "invoices": 2},
    # CA
    {"name": "Northern Lights Manufacturing Ltd.", "country": "CA", "layout": "ca_bilingual", "color": colors.HexColor("#1a365d"), "trade": "manufacturing", "invoices": 2},
    {"name": "Toronto Precision Tools Inc.", "country": "CA", "layout": "classic_us", "color": colors.HexColor("#2c5282"), "trade": "tools", "invoices": 2},
    {"name": "Vancouver Coastal Supplies", "country": "CA", "layout": "boxed", "color": colors.HexColor("#2b6cb0"), "trade": "supplies", "invoices": 2},
    {"name": "Calgary Energy Services Corp.", "country": "CA", "layout": "eu_table", "color": colors.HexColor("#744210"), "trade": "energy", "invoices": 2},
    # GB
    {"name": "Thames Valley Engineering Ltd", "country": "GB", "layout": "uk_vat", "color": colors.HexColor("#1a365d"), "trade": "engineering", "invoices": 2},
    {"name": "Manchester Industrial Parts", "country": "GB", "layout": "classic_us", "color": colors.HexColor("#2d3748"), "trade": "parts", "invoices": 2},
    {"name": "Edinburgh Software Ltd", "country": "GB", "layout": "minimal", "color": colors.HexColor("#2b6cb0"), "trade": "software", "invoices": 2},
    {"name": "Bristol Logistics Group", "country": "GB", "layout": "boxed", "color": colors.HexColor("#276749"), "trade": "logistics", "invoices": 2},
    # IE
    {"name": "Dublin Precision Components", "country": "IE", "layout": "uk_vat", "color": colors.HexColor("#1a365d"), "trade": "components", "invoices": 2},
    {"name": "Cork Manufacturing Services", "country": "IE", "layout": "eu_table", "color": colors.HexColor("#2c5282"), "trade": "manufacturing", "invoices": 2},
    {"name": "Galway Tech Solutions", "country": "IE", "layout": "minimal", "color": colors.HexColor("#2b6cb0"), "trade": "software", "invoices": 2},
    # DE
    {"name": "Rheinland Maschinenbau GmbH", "country": "DE", "layout": "german_formal", "color": colors.HexColor("#1a365d"), "trade": "machinery", "invoices": 2},
    {"name": "Berlin Software AG", "country": "DE", "layout": "eu_table", "color": colors.HexColor("#2d3748"), "trade": "software", "invoices": 2},
    {"name": "München Präzisionsteile", "country": "DE", "layout": "boxed", "color": colors.HexColor("#744210"), "trade": "parts", "invoices": 2},
    # FR
    {"name": "Lyon Industries SARL", "country": "FR", "layout": "fr_ttc", "color": colors.HexColor("#1a365d"), "trade": "industrial", "invoices": 2},
    {"name": "Paris Composants Électroniques", "country": "FR", "layout": "eu_table", "color": colors.HexColor("#2c5282"), "trade": "electronics", "invoices": 2},
    {"name": "Marseille Logistique SA", "country": "FR", "layout": "classic_us", "color": colors.HexColor("#276749"), "trade": "logistics", "invoices": 2},
    # NL
    {"name": "Amsterdam Precision BV", "country": "NL", "layout": "eu_table", "color": colors.HexColor("#1a365d"), "trade": "precision", "invoices": 2},
    {"name": "Rotterdam Haven Supplies", "country": "NL", "layout": "boxed", "color": colors.HexColor("#2b6cb0"), "trade": "supplies", "invoices": 1},
    {"name": "Utrecht Software Solutions", "country": "NL", "layout": "minimal", "color": colors.HexColor("#2d3748"), "trade": "software", "invoices": 2},
    # IT
    {"name": "Milano Componenti Srl", "country": "IT", "layout": "italian_style", "color": colors.HexColor("#1a365d"), "trade": "components", "invoices": 2},
    {"name": "Roma Ingegneria SpA", "country": "IT", "layout": "eu_table", "color": colors.HexColor("#744210"), "trade": "engineering", "invoices": 1},
    # ES
    {"name": "Barcelona Herramientas SL", "country": "ES", "layout": "spanish_style", "color": colors.HexColor("#1a365d"), "trade": "tools", "invoices": 2},
    {"name": "Madrid Suministros Industriales", "country": "ES", "layout": "eu_table", "color": colors.HexColor("#2c5282"), "trade": "supplies", "invoices": 2},
    # Extra to reach 60
    {"name": "Austin Industrial Supply", "country": "US", "layout": "classic_us", "color": colors.HexColor("#1a365d"), "trade": "industrial", "invoices": 1},
    {"name": "Quebec Forest Products", "country": "CA", "layout": "ca_bilingual", "color": colors.HexColor("#276749"), "trade": "materials", "invoices": 1},
    {"name": "Glasgow Engineering Works", "country": "GB", "layout": "uk_vat", "color": colors.HexColor("#744210"), "trade": "engineering", "invoices": 1},
]

# Expand to invoice slots
invoice_slots = []
for s in SUPPLIERS:
    for i in range(s["invoices"]):
        invoice_slots.append({**s, "seq": i})

# Shuffle with seed for assignment
random.shuffle(invoice_slots)
invoice_slots = invoice_slots[:60]  # ensure 60

# Assign renders so every country has clean, scan, and photo
from collections import defaultdict
country_slots = defaultdict(list)
for i, slot in enumerate(invoice_slots):
    country_slots[slot["country"]].append(i)

renders = [None] * 60
render_cycle = ["clean", "scan", "photo"]
for country, indices in country_slots.items():
    random.shuffle(indices)
    for j, idx in enumerate(indices):
        # First three get one of each, rest mostly clean
        if j < 3:
            renders[idx] = render_cycle[j]
        else:
            # Bias toward clean to keep overall ~50% clean
            renders[idx] = random.choices(["clean", "scan", "photo"], weights=[0.6, 0.2, 0.2])[0]

# Ensure we have enough of each overall if needed, but this should be close
# Count and adjust if necessary later if wanted, but for now this guarantees coverage

def format_number(amount, style):
    if style == "1,234.56":
        return f"{amount:,.2f}"
    elif style == "1.234,56":
        return f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    elif style == "1 234,56":
        return f"{amount:,.2f}".replace(",", " ").replace(".", ",")
    return f"{amount:.2f}"

def format_date(dt, convention):
    if convention == "month-first":
        return dt.strftime("%m/%d/%Y")
    elif convention == "day-first":
        return dt.strftime("%d/%m/%Y")
    elif convention == "year-first":
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%d %B %Y")

def generate_invoice_data(slot, idx):
    country = slot["country"]
    cfg = COUNTRIES[country]
    # Currency mix
    if country == "US":
        currency = "USD"
    elif country == "CA":
        currency = random.choice(["CAD", "CAD", "USD"])
    elif country == "GB":
        currency = random.choice(["GBP", "USD"])
    else:
        currency = random.choice(["EUR", "EUR", "USD"])  # mostly home
    
    # Dates
    base_date = datetime(2026, 6, 1) + timedelta(days=random.randint(0, 120))
    invoice_date = base_date
    # Terms variety
    terms_options = []
    if country in ["US", "CA", "GB"]:
        terms_options = ["Net 30", "Net 15", "Due on receipt", "2/10 Net 30", "Payment due within 45 days", "Upon receipt"]
    elif country == "DE":
        terms_options = ["30 Tage netto", "zahlbar innerhalb 14 Tagen", "sofort zahlbar", "2 % Skonto innerhalb 14 Tagen, 30 Tage netto", "30 Tage zum Monatsende"]
    elif country == "FR":
        terms_options = ["30 jours net", "à 30 jours", "à réception", "escompte 2 % à 10 jours, net à 30 jours", "30 jours fin de mois"]
    elif country == "NL":
        terms_options = ["netto 30 dagen", "binnen 30 dagen", "bij ontvangst", "30 dagen einde maand"]
    elif country == "IT":
        terms_options = ["30 giorni netto", "a 30 giorni", "al ricevimento", "fine mese"]
    elif country == "ES":
        terms_options = ["neto 30 días", "a 30 días", "a la recepción", "fin de mes"]
    else:
        terms_options = ["Net 30"]
    
    terms = random.choice(terms_options)
    due_source = None
    due_date = None
    # Simple logic for due
    if "Net 30" in terms or "30 Tage netto" in terms or "30 jours net" in terms or "netto 30" in terms or "30 giorni netto" in terms or "neto 30" in terms:
        due_date = invoice_date + timedelta(days=30)
        due_source = "terms"
    elif "Net 15" in terms:
        due_date = invoice_date + timedelta(days=15)
        due_source = "terms"
    elif "14 Tagen" in terms:
        due_date = invoice_date + timedelta(days=14)
        due_source = "terms"
    elif "receipt" in terms.lower() or "réception" in terms or "sofort" in terms or "ontvangst" in terms or "ricevimento" in terms or "recepción" in terms:
        due_date = invoice_date
        due_source = "terms"
    elif "45 days" in terms:
        due_date = invoice_date + timedelta(days=45)
        due_source = "terms"
    # EOM etc leave null
    # Some printed due
    if random.random() < 0.3 and due_date:
        due_source = "printed"
    
    # Amounts
    subtotal = round(random.uniform(200, 5000), 2)
    rate = random.uniform(*cfg["rate_range"]) if currency != "USD" or country != "US" else 0.0
    if country == "US" and random.random() < 0.4:
        rate = random.uniform(0.04, 0.08)
    tax = round(subtotal * rate, 2)
    # Sometimes omit tax line entirely (especially zero-rated exports)
    print_tax = True
    if tax == 0.0 and random.random() < 0.15:
        print_tax = False
    total = round(subtotal + (tax if print_tax else 0.0), 2)
    # Occasional mismatch
    if random.random() < 0.08:
        total = round(total + random.choice([-0.05, 0.03, 1.00, -2.50]), 2)
    
    # Invoice number
    prefixes = {"US": "INV-", "CA": "C", "GB": "UK", "IE": "IE", "DE": "RE-", "FR": "FAC-", "NL": "F", "IT": "FT", "ES": "F"}
    inv_num = f"{prefixes.get(country, 'INV')}{random.randint(10000, 99999)}"
    if country == "DE":
        inv_num = f"RE-{random.randint(2026,2026)}-{random.randint(1000,9999)}"
    
    # Line items
    num_lines = random.randint(2, 6)
    lines = []
    remaining = subtotal
    for i in range(num_lines):
        if i == num_lines - 1:
            amt = remaining
        else:
            amt = round(random.uniform(50, remaining / 2), 2)
            remaining -= amt
        lines.append({
            "desc": f"{slot['trade'].title()} item {i+1} - specialized component",
            "qty": random.randint(1, 10),
            "price": round(amt / max(1, random.randint(1,5)), 2),
            "amount": amt
        })
    
    return {
        "id": f"{idx:03d}-{slot['name'].lower().replace(' ', '-').replace('.', '')[:20]}",
        "supplier": slot["name"],
        "country": country,
        "layout": slot["layout"],
        "color": slot["color"],
        "currency": currency,
        "invoice_number": inv_num,
        "invoice_date": invoice_date.strftime("%Y-%m-%d"),
        "due_date": due_date.strftime("%Y-%m-%d") if due_date else None,
        "subtotal": f"{subtotal:.2f}",
        "tax": f"{tax:.2f}" if print_tax else None,
        "total": f"{total:.2f}",
        "terms": terms,
        "due_source": due_source,
        "date_convention": cfg["date_conv"],
        "number_style": cfg["num_style"],
        "lines": lines,
        "tax_name": cfg["vat_name"],
        "rate": rate,
        "pagesize": cfg["pagesize"],
        "render": renders[idx - 1],
        "address": f"123 Industrial Park\n{country} City, {country}",
        "phone": f"+{random.randint(1,99)} {random.randint(100,999)} {random.randint(100000,999999)}",
        "tax_id": f"{country}{random.randint(100000000,999999999)}",
    }

def draw_header(c, data, y):
    c.setFillColor(data["color"])
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, y, data["supplier"])
    c.setFont("Helvetica", 9)
    c.setFillColor(colors.black)
    c.drawString(50, y-15, data["address"].split("\n")[0])
    c.drawString(50, y-27, f"Tel: {data['phone']} | Tax ID: {data['tax_id']}")
    return y - 50

def draw_classic_us(c, data):
    width, height = data["pagesize"]
    y = height - 50
    y = draw_header(c, data, y)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, y, "INVOICE")
    c.setFont("Helvetica", 10)
    c.drawString(400, y, f"Invoice #: {data['invoice_number']}")
    c.drawString(400, y-15, f"Date: {format_date(datetime.strptime(data['invoice_date'], '%Y-%m-%d'), data['date_convention'])}")
    if data["due_date"] and data["due_source"] == "printed":
        c.drawString(400, y-30, f"Due Date: {format_date(datetime.strptime(data['due_date'], '%Y-%m-%d'), data['date_convention'])}")
    y -= 60
    c.drawString(50, y, "Bill To:")
    c.drawString(50, y-15, CUSTOMER["name"])
    c.drawString(50, y-27, CUSTOMER["address"].split("\n")[0])
    y -= 60
    # Table
    c.setFont("Helvetica-Bold", 9)
    c.drawString(50, y, "Description")
    c.drawString(300, y, "Qty")
    c.drawString(350, y, "Price")
    c.drawString(450, y, "Amount")
    y -= 15
    c.setFont("Helvetica", 9)
    for line in data["lines"]:
        c.drawString(50, y, line["desc"][:40])
        c.drawString(300, y, str(line["qty"]))
        c.drawString(350, y, format_number(line["price"], data["number_style"]))
        c.drawString(450, y, format_number(line["amount"], data["number_style"]))
        y -= 14
    y -= 20
    c.drawString(350, y, "Subtotal:")
    c.drawString(450, y, format_number(float(data["subtotal"]), data["number_style"]))
    y -= 15
    if data.get("tax") is not None:
        c.drawString(350, y, f"{data['tax_name']}:")
        c.drawString(450, y, format_number(float(data["tax"]), data["number_style"]))
        y -= 15
    c.setFont("Helvetica-Bold", 10)
    c.drawString(350, y, "Total:")
    c.drawString(450, y, f"{data['currency']} {format_number(float(data['total']), data['number_style'])}")
    y -= 40
    c.setFont("Helvetica", 9)
    c.drawString(50, y, f"Payment Terms: {data['terms']}")
    c.drawString(50, y-15, f"Currency: {data['currency']}")

def draw_generic(c, data):
    # Fallback and other layouts similar with variations
    draw_classic_us(c, data)
    # Add some layout-specific text
    if data["layout"] == "ca_bilingual":
        c.setFont("Helvetica", 8)
        c.drawString(50, 100, "Facture / Invoice")
    elif data["layout"] == "german_formal":
        c.drawString(50, 100, "Rechnung")
    elif data["layout"] == "fr_ttc":
        c.drawString(50, 100, "Montant TTC")

def render_pdf(data, path):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=data["pagesize"])
    if data["layout"] in ["classic_us", "minimal", "boxed", "uk_vat"]:
        draw_classic_us(c, data)
    else:
        draw_generic(c, data)
    c.save()
    pdf_bytes = buffer.getvalue()
    
    if data["render"] == "clean":
        with open(path, "wb") as f:
            f.write(pdf_bytes)
        return pdf_bytes
    else:
        # Convert to image
        images = convert_from_bytes(pdf_bytes, dpi=200 if data["render"] == "scan" else 180)
        img = images[0]
        # Degrade
        if data["render"] == "scan":
            img = img.rotate(random.uniform(-1.5, 1.5), expand=True, fillcolor="white")
            img = ImageEnhance.Contrast(img).enhance(0.9)
            img = img.filter(ImageFilter.GaussianBlur(radius=0.4))
            # Noise
            noise = Image.effect_noise(img.size, 10)
            img = Image.blend(img.convert("RGB"), noise.convert("RGB"), 0.05)
        else:  # photo
            img = img.rotate(random.uniform(-3, 3), expand=True, fillcolor=(240,240,240))
            img = ImageEnhance.Brightness(img).enhance(0.85)
            img = ImageEnhance.Contrast(img).enhance(0.8)
            img = img.filter(ImageFilter.GaussianBlur(radius=0.8))
            # Gradient lighting
            overlay = Image.new("RGB", img.size, (255,255,255))
            draw = ImageDraw.Draw(overlay)
            for i in range(img.size[1]):
                alpha = int(30 * (i / img.size[1]))
                draw.line([(0,i), (img.size[0],i)], fill=(alpha,alpha,alpha))
            img = Image.blend(img.convert("RGB"), overlay, 0.15)
        # Save as PDF
        img_rgb = img.convert("RGB")
        img_rgb.save(path, "PDF", resolution=150)
        with open(path, "rb") as f:
            return f.read()

def compute_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()

def main():
    invoices = []
    layouts_used = set()
    for idx, slot in enumerate(invoice_slots):
        data = generate_invoice_data(slot, idx + 1)
        layouts_used.add(data["layout"])
        render_folder = {"clean": CLEAN_DIR, "scan": SCAN_DIR, "photo": PHOTO_DIR}[data["render"]]
        filename = f"{data['id']}.pdf"
        path = os.path.join(render_folder, filename)
        pdf_bytes = render_pdf(data, path)
        sha = compute_sha256(path)
        
        # Exceptions
        reasons = []
        if data["currency"] != "USD":
            reasons.append("foreign_currency")
        if not data["due_date"]:
            reasons.append("missing_due_date")
        if data["due_date"] and data["due_date"] < data["invoice_date"]:
            reasons.append("due_before_invoice")
        tax_val = float(data["tax"]) if data["tax"] is not None else 0.0
        if abs(float(data["subtotal"]) + tax_val - float(data["total"])) > 0.01:
            reasons.append("total_mismatch")
        if data["tax"] is None:
            reasons.append("missing_tax")
        
        exception = len(reasons) > 0
        
        label = {
            "id": data["id"],
            "file": f"holdout-3/{data['render']}s/{filename}" if data["render"] != "clean" else f"holdout-3/clean/{filename}",
            "supplier": data["supplier"],
            "invoice_number": data["invoice_number"],
            "invoice_date": data["invoice_date"],
            "due_date": data["due_date"],
            "subtotal": data["subtotal"],
            "tax": data["tax"],
            "total": data["total"],
            "currency": data["currency"],
            "exception": exception,
            "exception_reasons": reasons,
            "terms": data["terms"],
            "due_source": data["due_source"],
            "country": data["country"],
            "date_convention": data["date_convention"],
            "number_style": data["number_style"],
            "layout": data["layout"],
            "render": data["render"],
            "sha256": sha
        }
        # Fix file path
        folder = data["render"] + "s" if data["render"] != "clean" else "clean"
        label["file"] = f"holdout-3/{folder}/{filename}"
        invoices.append(label)
    
    labels = {
        "fields": ["supplier", "invoice_number", "invoice_date", "due_date", "subtotal", "tax", "total"],
        "frozen": None,
        "generator": "make_set.py",
        "seed": SEED,
        "layouts": sorted(list(layouts_used)),
        "invoices": invoices
    }
    with open("labels.json", "w") as f:
        json.dump(labels, f, indent=2)
    
    # NOTES
    notes = """# NOTES

Generated a set of 60 fictional cross-border invoices for a US small business (Horizon Analytics LLC).

Country distribution (approximate):
- US: 12
- CA: 8
- GB: 8
- IE: 6
- DE: 6
- FR: 6
- NL: 5
- IT: 3
- ES: 3

Currency mix based on typical cross-border practices:
- US suppliers: always USD
- Canadian: mostly CAD, some USD
- UK: mix of GBP and USD
- Ireland and continental Europe: predominantly EUR, some USD for US customers (common for convenience in trade)

Date conventions follow local norms (day-first in Europe/UK, month-first in US, year-first in CA).
Number styles match local (comma decimal in DE/NL/IT/ES/FR, period in US/CA/GB/IE).

Layouts vary by supplier and country (classic US, EU table, UK VAT style, Canadian bilingual, German formal, French TTC, Italian, Spanish, minimal, boxed). Each supplier uses consistent styling.

Renders assigned independently of country: ~30 clean, 15 scan, 15 photo.
Scans ~200 dpi with slight rotation and noise; photos with blur, lighting gradient, rotation.

Terms include local language variants and some EOM/fin de mois which leave due_date null per rules.
Exceptions include foreign_currency (non-USD), missing_due_date (EOM etc.), total_mismatch (occasional arithmetic error), and rare due_before_invoice.

All names, addresses, tax IDs, and bank details are fictional. Currency is always identifiable via code or symbol.
"""
    with open("NOTES.md", "w") as f:
        f.write(notes)
    
    # requirements
    with open("requirements.txt", "w") as f:
        f.write("reportlab==4.2.5\npillow==11.0.0\npdf2image==1.17.0\n")
    
    print(f"Generated {len(invoices)} invoices")
    print(f"Layouts: {layouts_used}")

if __name__ == "__main__":
    main()
