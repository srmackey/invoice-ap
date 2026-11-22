"""Write the practice set: sandbox/practice/ and its labels.json.

Forty one-page invoices for developing the reader-misses work: dates in each
supplier's own format, day-first and year-first suppliers, amounts in USD,
CAD, GBP, and EUR with decimal commas, payment terms in English, French,
German, Dutch, Italian, and Spanish (including the forms the terms rule
leaves for a person), dates that do not parse, and the arithmetic exceptions.

Date patterns and number styles come from CLDR, through Babel, for each
supplier's locale (en-US, en-CA, en-GB, de-DE, fr-FR, nl-NL, it-IT, es-ES).
The list is written in the reader-misses spec before this script was. Nothing
here is drawn from a holdout. This set is practice, not a benchmark: it is not
claimed and may be regenerated.

Labels follow the rules the reader is moving to: foreign_currency and the
wider terms rule. The rules are the script's own copy, so the labels do not
lean on the reader they will judge. A printed due date that is not a date
("TBC") is labeled null with missing_due_date; the reader raises
unparsed_due_date for it, and both are an exception.

Needs reportlab and babel (sandbox/.venv), and DejaVu Sans for accented text.

Run from the checkout:
  python sandbox/make_practice.py
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from babel.dates import format_date
from babel.numbers import format_decimal
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "practice"
SEED = 20261122
FIELDS = ("supplier", "invoice_number", "invoice_date", "due_date", "subtotal", "tax", "total")
CENT = Decimal("0.01")

FONT_DIR = Path("C:/Windows/Fonts")
if (FONT_DIR / "DejaVuSans.ttf").is_file():
    pdfmetrics.registerFont(TTFont("Body", str(FONT_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("Bold", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
    BODY, BOLD = "Body", "Bold"
else:
    BODY, BOLD = "Helvetica", "Helvetica-Bold"

# CLDR date patterns per locale, as written in the spec. Babel renders them.
LOCALES = {
    "en_US": {"country": "US", "short": "M/d/yy", "medium": "MMM d, y", "long": "MMMM d, y", "lang": "en", "tax": ("0.00", "0.06", "0.0825")},
    "en_CA": {"country": "CA", "short": "y-MM-dd", "medium": "MMM d, y", "long": "MMMM d, y", "lang": "en", "tax": ("0.05", "0.13")},
    "en_GB": {"country": "GB", "short": "dd/MM/y", "medium": "d MMM y", "long": "d MMMM y", "lang": "en", "tax": ("0.20",)},
    "de_DE": {"country": "DE", "short": "dd.MM.yy", "medium": "dd.MM.y", "long": "d. MMMM y", "lang": "de", "tax": ("0.19",)},
    "fr_FR": {"country": "FR", "short": "dd/MM/y", "medium": "d MMM y", "long": "d MMMM y", "lang": "fr", "tax": ("0.20",)},
    "nl_NL": {"country": "NL", "short": "dd-MM-y", "medium": "d MMM y", "long": "d MMMM y", "lang": "nl", "tax": ("0.21",)},
    "it_IT": {"country": "IT", "short": "dd/MM/yy", "medium": "d MMM y", "long": "d MMMM y", "lang": "it", "tax": ("0.22",)},
    "es_ES": {"country": "ES", "short": "d/M/yy", "medium": "d MMM y", "long": "d 'de' MMMM 'de' y", "lang": "es", "tax": ("0.21",)},
}

HOME_CURRENCY = {"US": "USD", "CA": "CAD", "GB": "GBP"}

# Terms per language: (printed text, days or None when the rule leaves it null).
TERMS = {
    "en": [("Net 30", 30), ("30 days net", 30), ("Payment due within 14 days", 14), ("Payment terms: 45 days", 45),
           ("Due on receipt", 0), ("2% 10 days, net 30", 30), ("Net 30 EOM", None), ("30 days from delivery", None)],
    "de": [("30 Tage netto", 30), ("zahlbar innerhalb 14 Tagen", 14), ("sofort zahlbar", 0),
           ("2 % Skonto innerhalb 10 Tagen, 30 Tage netto", 30), ("30 Tage zum Monatsende", None)],
    "fr": [("30 jours net", 30), ("à 45 jours", 45), ("à réception", 0), ("30 jours fin de mois", None)],
    "nl": [("netto 30 dagen", 30), ("binnen 14 dagen", 14), ("bij ontvangst", 0), ("30 dagen einde maand", None)],
    "it": [("a 30 giorni", 30), ("60 giorni netto", 60), ("al ricevimento", 0), ("30 giorni fine mese", None)],
    "es": [("a 30 días", 30), ("neto 60 días", 60), ("a la recepción", 0), ("30 días fin de mes", None)],
}

WORDS = {
    "en": {"invoice": "Invoice", "number": "Invoice No.", "date": "Invoice date", "due": "Due date", "terms": "Terms",
           "subtotal": "Subtotal", "tax": "Tax", "total": "Total", "amount_due": "Amount Due", "deposit": "Less deposit"},
    "de": {"invoice": "Rechnung", "number": "Rechnungsnr.", "date": "Rechnungsdatum", "due": "Fällig am", "terms": "Zahlungsbedingungen",
           "subtotal": "Zwischensumme", "tax": "MwSt.", "total": "Gesamtbetrag", "amount_due": "Zahlbetrag", "deposit": "Anzahlung"},
    "fr": {"invoice": "Facture", "number": "N° de facture", "date": "Date de facture", "due": "Échéance", "terms": "Conditions de paiement",
           "subtotal": "Total HT", "tax": "TVA", "total": "Total TTC", "amount_due": "Net à payer", "deposit": "Acompte"},
    "nl": {"invoice": "Factuur", "number": "Factuurnummer", "date": "Factuurdatum", "due": "Vervaldatum", "terms": "Betalingsvoorwaarden",
           "subtotal": "Subtotaal", "tax": "BTW", "total": "Totaal", "amount_due": "Te betalen", "deposit": "Aanbetaling"},
    "it": {"invoice": "Fattura", "number": "Fattura n.", "date": "Data fattura", "due": "Scadenza", "terms": "Termini di pagamento",
           "subtotal": "Imponibile", "tax": "IVA", "total": "Totale", "amount_due": "Totale da pagare", "deposit": "Acconto"},
    "es": {"invoice": "Factura", "number": "N.º de factura", "date": "Fecha de factura", "due": "Vencimiento", "terms": "Forma de pago",
           "subtotal": "Base imponible", "tax": "IVA", "total": "Total", "amount_due": "Importe a pagar", "deposit": "Anticipo"},
}

NOT_A_DATE = {"en": "TBC", "de": "nach Vereinbarung", "fr": "à convenir", "nl": "n.t.b.", "it": "da concordare", "es": "a convenir"}

# Suppliers: (locale, name, prefix, invoices). Names are invented.
SUPPLIERS = [
    ("en_US", "Copper Creek Supply", "CCS", 3), ("en_US", "Halvorsen Tool & Die", "HTD", 2),
    ("en_US", "Bright Harbor Printing", "BHP", 2), ("en_US", "Larkspur Office Systems", "LOS", 1),
    ("en_CA", "Northgate Fasteners Ltd.", "NGF", 3), ("en_CA", "Muskoka Timber Works", "MTW", 2),
    ("en_GB", "Pennine Instruments Ltd", "PIL", 3), ("en_GB", "Solent Marine Fittings", "SMF", 2),
    ("de_DE", "Weserland Antriebstechnik GmbH", "WAT", 3), ("de_DE", "Kranich Elektro KG", "KEK", 2),
    ("fr_FR", "Ateliers Vauclair SARL", "AV", 3), ("fr_FR", "Lumière Emballages SAS", "LE", 2),
    ("nl_NL", "Van Leeuwen Kunststoffen B.V.", "VLK", 2), ("nl_NL", "Brandsma Logistiek", "BL", 2),
    ("it_IT", "Ferramenta Rovigo S.r.l.", "FR", 2), ("it_IT", "Tessiture Lariane S.p.A.", "TL", 2),
    ("es_ES", "Herrajes Albacete S.L.", "HA", 2), ("es_ES", "Talleres Duero S.A.", "TD", 2),
]

LINES = ["Bearings, box of 50", "Hydraulic hose, 10 m", "Cable ties, case", "Labels, roll", "Service call",
         "Gaskets, set", "Pallet wrap, roll", "Fasteners, assorted", "Calibration", "Freight"]


def money(value) -> Decimal:
    return Decimal(value).quantize(CENT)


def number_text(value: Decimal, locale: str) -> str:
    return format_decimal(value, locale=locale, format="#,##0.00").replace("\u202f", " ").replace("\u00a0", " ")


def amount_text(value: Decimal, inv: dict) -> str:
    n = number_text(value, inv["locale"])
    currency = inv["currency"]
    if currency == HOME_CURRENCY.get(inv["country"]) == "USD":
        return f"${n}"
    if currency == "CAD":
        return f"${n} CAD"
    if currency == "GBP":
        return f"£{n}"
    if currency == "EUR":
        return f"€ {n}" if inv["locale"] == "nl_NL" else f"{n} €"
    return f"{n} USD" if inv["country"] in ("DE", "FR", "NL", "IT", "ES") else f"USD {n}"


def date_text(day: date, inv: dict) -> str:
    return format_date(day, LOCALES[inv["locale"]][inv["date_style"]], locale=inv["locale"])


def build(rng: random.Random) -> list[dict]:
    invoices, n = [], 0
    for locale, name, prefix, count in SUPPLIERS:
        info = LOCALES[locale]
        country, lang = info["country"], info["lang"]
        style = rng.choice(("short", "short", "medium", "long"))
        start = rng.randint(1000, 8000)
        for k in range(count):
            n += 1
            home = HOME_CURRENCY.get(country, "EUR")
            currency = home if country == "US" or rng.random() < 0.75 else "USD"
            day = date(2026, rng.randint(1, 10), rng.randint(1, 28))
            inv = {
                "id": f"p{n:02d}-{prefix.lower()}",
                "locale": locale, "country": country, "lang": lang, "supplier": name,
                "number": f"{prefix}-{start + k * rng.randint(3, 40)}", "date": day, "date_style": style,
                "currency": currency, "tax_rate": rng.choice(info["tax"]),
                "lines": [(rng.choice(LINES), rng.randint(1, 12), f"{rng.randint(800, 60000) / 100:.2f}")
                          for _ in range(rng.randint(1, 4))],
            }
            options = TERMS[lang]
            nulls = [t for t in options if t[1] is None]
            terms, days = rng.choice(nulls if rng.random() < 0.25 else [t for t in options if t[1] is not None])
            roll = rng.random()
            if roll < 0.55:
                inv["terms"], inv["days"] = terms, days
            elif roll < 0.85:
                inv["due"] = day + timedelta(days=rng.choice((14, 30, 45, 60)))
                if rng.random() < 0.4:
                    inv["terms"], inv["days"] = terms, days
            else:
                inv["due_text"] = NOT_A_DATE[lang]
            invoices.append(inv)
    # The arithmetic exceptions and the amount-due cases, on fixed invoices so they do not drift.
    invoices[2]["mismatch"] = True
    invoices[17]["mismatch"] = True
    invoices[9]["due"] = invoices[9]["date"] - timedelta(days=5)
    invoices[9].pop("due_text", None)
    invoices[5]["amount_due_only"] = True
    invoices[28]["deposit"] = "200.00"
    return invoices


def amounts(inv: dict) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    subtotal = sum((money(Decimal(q) * Decimal(u)) for _d, q, u in inv["lines"]), Decimal("0.00"))
    tax = money(subtotal * Decimal(inv["tax_rate"]))
    total = subtotal + tax + (Decimal("10.00") if inv.get("mismatch") else Decimal("0.00"))
    printed = total - money(inv["deposit"]) if inv.get("deposit") else total
    return subtotal, tax, total, printed


def label(inv: dict) -> dict:
    subtotal, tax, total, printed_total = amounts(inv)
    if inv.get("due"):
        due, source = inv["due"], "printed"
    elif inv.get("due_text"):
        due, source = None, None
    elif inv.get("days") is not None:
        due, source = inv["date"] + timedelta(days=inv["days"]), "terms"
    else:
        due, source = None, None
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
        "currency": inv["currency"],
        "country": inv["country"],
        "locale": inv["locale"].replace("_", "-"),
        "date_style": inv["date_style"],
        "number_style": number_text(Decimal("1234.56"), inv["locale"]),
        "terms": inv.get("terms"),
        "due_source": source,
        "due_printed_not_a_date": bool(inv.get("due_text")),
    }
    reasons = [] if inv["currency"] == "USD" else ["foreign_currency"]
    reasons += [f"missing_{name}" for name in FIELDS if row[name] is None]
    if due and due < inv["date"]:
        reasons.append("due_before_invoice")
    if abs(subtotal + tax - printed_total) > CENT:
        reasons.append("total_mismatch")
    row["exception"] = bool(reasons)
    row["exception_reasons"] = reasons
    return row


def write_pdf(inv: dict, path: Path) -> None:
    w = WORDS[inv["lang"]]
    body = ParagraphStyle("body", fontName=BODY, fontSize=10, leading=13)
    head = ParagraphStyle("head", fontName=BOLD, fontSize=17, leading=21)
    subtotal, tax, total, printed_total = amounts(inv)
    page = letter if inv["country"] in ("US", "CA") else A4
    story = [Paragraph(inv["supplier"], head), Paragraph(w["invoice"], body), Spacer(1, 0.15 * inch)]
    grid = [[w["number"], inv["number"]], [w["date"], date_text(inv["date"], inv)]]
    if inv.get("due"):
        grid.append([w["due"], date_text(inv["due"], inv)])
    if inv.get("due_text"):
        grid.append([w["due"], inv["due_text"]])
    if inv.get("terms"):
        grid.append([w["terms"], inv["terms"]])
    header = Table(grid, colWidths=[2.3 * inch, 2.6 * inch])
    header.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), BODY), ("FONTNAME", (0, 0), (0, -1), BOLD),
                                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey)]))
    story += [header, Spacer(1, 0.25 * inch)]
    rows = [["", "", "", ""]]
    for desc, qty, unit in inv["lines"]:
        rows.append([desc, str(qty), amount_text(money(unit), inv), amount_text(money(Decimal(qty) * Decimal(unit)), inv)])
    items = Table(rows, colWidths=[3.1 * inch, 0.6 * inch, 1.4 * inch, 1.5 * inch])
    items.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), BODY), ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                               ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black)]))
    story += [items, Spacer(1, 0.2 * inch)]
    totals = [[w["subtotal"], amount_text(subtotal, inv)], [w["tax"], amount_text(tax, inv)]]
    if inv.get("deposit"):
        totals.append([w["total"], amount_text(total, inv)])
        totals.append([w["deposit"], "-" + amount_text(money(inv["deposit"]), inv)])
        totals.append([w["amount_due"], amount_text(printed_total, inv)])
    elif inv.get("amount_due_only"):
        totals.append([w["amount_due"], amount_text(printed_total, inv)])
    else:
        totals.append([w["total"], amount_text(printed_total, inv)])
    box = Table(totals, colWidths=[1.9 * inch, 1.6 * inch], hAlign="RIGHT")
    box.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), BODY), ("FONTNAME", (0, -1), (-1, -1), BOLD),
                             ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black)]))
    story.append(box)
    SimpleDocTemplate(str(path), pagesize=page, title=inv["id"]).build(story)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.pdf"):
        old.unlink()
    rows = []
    for inv in build(random.Random(SEED)):
        write_pdf(inv, OUT / f"{inv['id']}.pdf")
        rows.append(label(inv))
    labels = {
        "fields": list(FIELDS),
        "note": "Practice set for the reader-misses work. Not a benchmark and not claimed. Labels follow foreign_currency "
                "and the wider terms rule, which the reader may not implement yet.",
        "generator": "make_practice.py",
        "seed": SEED,
        "invoices": rows,
    }
    (OUT / "labels.json").write_text(json.dumps(labels, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} invoices to {OUT}")


if __name__ == "__main__":
    main()
