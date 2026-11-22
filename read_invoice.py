"""Read one supplier invoice.

Reads the PDF with Amazon Textract AnalyzeExpense (the default) or with a
local Docling Serve (the backup), and returns the seven fields plus the
exception rule from DESIGN.md. The page, sandbox/score.py, and the hosted
function call this same code.

Usage:
  python read_invoice.py path/to/invoice.pdf
  python read_invoice.py path/to/invoice.pdf --profile <profile>
  python read_invoice.py path/to/invoice.pdf --runner docling

Textract needs boto3 and AWS credentials (a profile, or the environment).
Docling needs the container in services/docling running.

The command appends one record to reads/reads.jsonl in this checkout.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dateutil import parser as date_parser

FIELDS = (
    "supplier",
    "invoice_number",
    "invoice_date",
    "due_date",
    "subtotal",
    "tax",
    "total",
)
TEXT_FIELDS = ("supplier", "invoice_number")
DATE_FIELDS = ("invoice_date", "due_date")
MONEY_FIELDS = ("subtotal", "tax", "total")
CENT = Decimal("0.01")
# Month words in French, German, Dutch, Italian, and Spanish, wide and
# abbreviated, from CLDR (Babel 2.17.0, read 2026-11-22). English names are
# left out because dateutil reads them already.
MONTH_WORDS = {
    "ene": 1, "enero": 1, "gen": 1, "gennaio": 1, "januar": 1, "januari": 1, "janv": 1, "janvier": 1,
    "febbraio": 2, "febrero": 2, "februar": 2, "februari": 2, "fevr": 2, "fevrier": 2, "févr": 2, "février": 2,
    "maart": 3, "mars": 3, "marz": 3, "marzo": 3, "mrt": 3, "mär": 3, "märz": 3,
    "abr": 4, "abril": 4, "aprile": 4, "avr": 4, "avril": 4,
    "mag": 5, "maggio": 5, "mai": 5, "mayo": 5, "mei": 5,
    "giu": 6, "giugno": 6, "juin": 6, "juni": 6, "junio": 6,
    "juil": 7, "juillet": 7, "juli": 7, "julio": 7, "lug": 7, "luglio": 7,
    "ago": 8, "agosto": 8, "aout": 8, "août": 8, "augustus": 8,
    "septembre": 9, "septiembre": 9, "set": 9, "settembre": 9,
    "octobre": 10, "octubre": 10, "okt": 10, "oktober": 10, "ott": 10, "ottobre": 10,
    "novembre": 11, "noviembre": 11,
    "decembre": 12, "dez": 12, "dezember": 12, "dic": 12, "dicembre": 12, "diciembre": 12, "déc": 12, "décembre": 12,
}
ENGLISH_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
                  "October", "November", "December")
_MONTH_WORD = re.compile(
    r"\b(" + "|".join(sorted(map(re.escape, MONTH_WORDS), key=len, reverse=True)) + r")\.?(?![^\W\d_])",
    re.IGNORECASE,
)
_NUMERIC_DATE = re.compile(r"^\s*(\d{1,4})\s*([./-])\s*(\d{1,2})\s*\2\s*(\d{2,4})\s*$")
# Two different fill-in dates: a date that reads the same against both has a
# day, a month, and a year of its own. One that differs was missing a part.
_DATE_DEFAULTS = (datetime(2001, 1, 1), datetime(2002, 2, 2))
EARLIEST_YEAR = 1900
DOCLING_URL = "http://127.0.0.1:5001/v1/convert/file"
DOCLING_TIMEOUT = 120
READS_PATH = Path(__file__).resolve().parent / "reads" / "reads.jsonl"
MONEY_LABELS = ("subtotal", "tax", "total")
HEADER_SKIP = {"invoice", "bill to"}
# Due-date labels in English, German, French, Dutch, Italian, and Spanish,
# lowercased and without accents. Textract types English ones itself, but
# leaves foreign ones untyped or types them as the invoice date.
DUE_LABELS = (
    "due date", "date due", "payment due date", "due by",
    "fallig am", "fallig", "falligkeit", "falligkeitsdatum", "zahlbar bis",
    "echeance", "date d'echeance", "date echeance", "date limite de paiement",
    "vervaldatum", "vervaldag", "uiterste betaaldatum",
    "scadenza", "data scadenza", "data di scadenza",
    "vencimiento", "fecha de vencimiento", "fecha vencimiento",
)
# Invoice-date labels in the same languages. Used only for a field Textract
# leaves untyped; one it typed keeps its type unless it is a due-date label.
INVOICE_DATE_LABELS = (
    "invoice date", "date of invoice", "rechnungsdatum", "date de facture", "date de la facture",
    "factuurdatum", "data fattura", "data della fattura", "fecha de factura", "fecha factura",
)
# Invoice-number labels in the same languages, for untyped fields only.
INVOICE_NUMBER_LABELS = (
    "invoice number", "invoice no", "invoice no.", "invoice #", "rechnungsnr", "rechnungsnr.", "rechnungsnummer",
    "rechnung nr", "rechnung nr.", "n de facture", "n° de facture", "no de facture", "numero de facture",
    "factuurnummer", "factuur nr", "factuurnr", "fattura n", "fattura n.", "numero fattura", "n. fattura",
    "n.o de factura", "n.º de factura", "no de factura", "numero de factura", "factura n", "factura no",
)
# Label words per field, for a field Textract leaves untyped and for the
# value printed beside a label on the page.
LABELS_FOR = {"due_date": DUE_LABELS, "invoice_date": INVOICE_DATE_LABELS, "invoice_number": INVOICE_NUMBER_LABELS}
FIELD_PREFIXES = (
    ("invoice_number", "invoice number"),
    ("invoice_date", "invoice date"),
    *[("due_date", label) for label in DUE_LABELS],
    ("due_date", "fällig am"), ("due_date", "fälligkeit"), ("due_date", "fälligkeitsdatum"),
    ("due_date", "échéance"), ("due_date", "date d'échéance"),
)
RUNNERS = ("textract", "docling")
DEFAULT_RUNNER = "textract"
DEFAULT_REGION = "us-east-1"
TEXTRACT_MODEL = "textract-analyze-expense"
TEXTRACT_TYPES = {
    "VENDOR_NAME": "supplier",
    "INVOICE_RECEIPT_ID": "invoice_number",
    "INVOICE_RECEIPT_DATE": "invoice_date",
    "DUE_DATE": "due_date",
    "SUBTOTAL": "subtotal",
    "TAX": "tax",
    "TOTAL": "total",
    "AMOUNT_DUE": "amount_due",
    "PAYMENT_TERMS": "terms",
}
# Read alongside the seven, and used only to fill them: the printed amount due
# stands in for a missing total, and printed terms set a missing due date.
EXTRAS = ("amount_due", "terms", "currency")
RAW_KEYS = FIELDS + EXTRAS
TERMS_PREFIXES = (
    ("terms", "payment terms"), ("terms", "terms"), ("terms", "zahlungsbedingungen"), ("terms", "zahlungsziel"),
    ("terms", "conditions de paiement"), ("terms", "modalites de paiement"), ("terms", "betalingsvoorwaarden"),
    ("terms", "termini di pagamento"), ("terms", "condizioni di pagamento"), ("terms", "condiciones de pago"),
    ("terms", "forma de pago"),
)
# A label Textract leaves untyped counts as a terms label when it holds one of these words.
TERMS_LABEL_WORDS = ("terms", "zahlung", "paiement", "betaling", "pagamento", "pago")
AMOUNT_DUE_PREFIXES = ("amount due", "balance due", "total due")
TERMS_LINE_MAX = 40


def blank(value) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return text == "" or text.lower() in {"null", "none", "n/a"}


def as_text(value):
    if blank(value):
        return None
    return " ".join(str(value).split())


def numeric_order(text: str | None) -> str | None:
    """How an all-numeric date orders its parts: "year" (2026-03-14), "day"
    (14/03/2026, only day-first reads it), "month" (03/14/2026), "dotted"
    (04.03.2026, read day-first), or "either" (03/04/2026). None for any
    date that is not all numbers."""
    found = _NUMERIC_DATE.match(text or "")
    if not found:
        return None
    first, sep, second = found[1], found[2], int(found[3])
    if len(first) == 4:
        return "year"
    first = int(first)
    if first > 12 and second <= 12:
        return "day"
    if second > 12 and first <= 12:
        return "month"
    return "dotted" if sep == "." else "either"


def english_months(text: str) -> str:
    """Month words in the five other languages, swapped for English, so dateutil reads them."""
    text = re.sub(r"\b1er\b", "1", text, flags=re.IGNORECASE)
    return _MONTH_WORD.sub(lambda m: ENGLISH_MONTHS[MONTH_WORDS[m[1].lower()] - 1], text)


def as_date(value, day_first: bool = False):
    """A calendar date from printed text, "unparsed" when the text is there but
    does not read as one, None when nothing is printed.

    A numeric date that can only be one order is read that way. A dotted one
    is read day-first. An ambiguous one is read month-first unless day_first
    says the page proved otherwise. Text with a day, a month, and a year is
    read strictly first, then fuzzily (a date inside a sentence). Anything
    missing a part, or outside 1900 through next year, is unparsed."""
    text = as_text(value)
    if text is None:
        return None
    order = numeric_order(text)
    if order == "year":
        options = {"yearfirst": True, "dayfirst": False}
    elif order in ("day", "dotted"):
        options = {"dayfirst": True}
    elif order == "month":
        options = {"dayfirst": False}
    else:
        options = {"dayfirst": day_first}
    text = english_months(text)
    for fuzzy in (False, True):
        try:
            first = date_parser.parse(text, default=_DATE_DEFAULTS[0], fuzzy=fuzzy, **options)
            second = date_parser.parse(text, default=_DATE_DEFAULTS[1], fuzzy=fuzzy, **options)
        except (ValueError, OverflowError):
            continue
        if first != second or not EARLIEST_YEAR <= first.year <= date.today().year + 1:
            return "unparsed"
        return first.date()
    return "unparsed"


_CURRENCY_TOKENS = re.compile(r"(?i)\b(?:eur|usd|gbp|cad|cdn)\b|us\$|ca\$|c\$|[€£$]")
_CURRENCY_SIGNS = (
    ("EUR", re.compile(r"€|\bEUR\b", re.IGNORECASE)),
    ("GBP", re.compile(r"£|\bGBP\b", re.IGNORECASE)),
    ("CAD", re.compile(r"\bCAD\b|\bCDN\b|\bC\$|\bCA\$", re.IGNORECASE)),
    ("USD", re.compile(r"\bUSD\b|\bUS\$", re.IGNORECASE)),
)
CURRENCIES = ("USD", "CAD", "GBP", "EUR")
# Currencies whose suppliers usually write a decimal comma. Used only for an
# amount that does not show its decimal mark itself, such as "1.234".
COMMA_DECIMAL_CURRENCIES = ("EUR",)


def currency_in(text) -> str | None:
    """The currency a piece of text names by sign or code. A bare "$" names none."""
    if blank(text):
        return None
    found = [code for code, sign in _CURRENCY_SIGNS if sign.search(str(text))]
    return found[0] if len(found) == 1 else None


def as_money(value, comma_decimal: bool = False):
    """Decimal from a printed amount, "unparsed" when it does not read as one.

    The amount shows its own decimal mark when it can: with both marks, the
    later one is the decimal ("1.234,56", "1,234.56"); a lone comma or dot
    followed by one or two digits is the decimal ("3872,40", "12.5"); followed
    by three it is grouping ("1,234"). Only a lone dot before three digits
    ("1.234") cannot say, and comma_decimal (a EUR page) reads it as grouping.
    Currency signs and codes, spaces, and apostrophes are dropped."""
    if blank(value):
        return None
    text = _CURRENCY_TOKENS.sub("", str(value).strip().lstrip(">"))
    text = re.sub(r"[\s\u00a0\u202f']", "", text)
    if not re.fullmatch(r"-?\d[\d.,]*", text):
        return "unparsed"
    dot, comma = text.rfind("."), text.rfind(",")
    if dot >= 0 and comma >= 0:
        decimal = "." if dot > comma else ","
    elif comma >= 0:
        decimal = "," if len(text) - comma - 1 in (1, 2) and text.count(",") == 1 else None
    elif dot >= 0:
        tail = len(text) - dot - 1
        decimal = None if tail == 3 and (comma_decimal or text.count(".") > 1) else "."
    else:
        decimal = None
    if decimal == ",":
        text = text.replace(".", "").replace(",", ".")
    elif decimal == ".":
        text = text.replace(",", "")
    else:
        text = text.replace(",", "").replace(".", "")
    try:
        return Decimal(text).quantize(CENT)
    except (InvalidOperation, ValueError):
        return "unparsed"


def decimal_comma_shown(value) -> bool:
    """True when a printed amount shows a decimal comma itself: "7.996,92",
    "5 784,33", "383,74". A US supplier never writes one."""
    if blank(value):
        return False
    text = re.sub(r"[\s\u00a0\u202f']", "", _CURRENCY_TOKENS.sub("", str(value)))
    if not re.fullmatch(r"-?\d[\d.,]*", text):
        return False
    comma = text.rfind(",")
    return comma > text.rfind(".") and len(text) - comma - 1 in (1, 2) and text.count(",") == 1


def page_currency(raw: dict) -> str | None:
    """The invoice's currency: what the runner read, else a sign or code on the
    amounts. None when nothing names one, which reads as US dollars."""
    if raw.get("currency") in CURRENCIES:
        return raw["currency"]
    found = {currency_in(raw.get(name)) for name in MONEY_FIELDS + ("amount_due",)} - {None}
    if len(found) == 1:
        return found.pop()
    foreign = sorted(found - {"USD"})
    return foreign[0] if foreign else None


UNCOMPUTABLE = "uncomputable"
TERMS_MAX_DAYS = 180
# Terms text is matched lowercased and without accents ("à réception" is "a reception").
_EOM = re.compile(
    r"\beom\b|\be\.o\.m\b|end of (?:the )?month|\bfollowing month\b|\bprox(?:imo)?\b|\bmfi\b"
    r"|monatsende|ende des monats|folgemonat|\bfin (?:de|du) mois\b|\bmois suivant\b"
    r"|\beinde (?:van de )?maand\b|\bfine mese\b|\bmese successivo\b|\bfin (?:de|del) mes\b|\bmes siguiente\b"
)
_ANCHOR_ELSEWHERE = re.compile(
    r"\b(?:from|after)\s+(?:the\s+)?(?:date\s+of\s+)?(?:delivery|shipment|dispatch|receipt of goods|goods|statement)"
    r"|\b(?:nach|ab)\s+(?:lieferung|erhalt der ware|warenerhalt|lieferdatum)"
    r"|\b(?:a compter de|apres|des)\s+(?:la\s+)?livraison|\bdate de livraison"
    r"|\bna\s+(?:levering|ontvangst van de goederen)"
    r"|\bdalla\s+(?:consegna|data di consegna)"
    r"|\bdesde\s+(?:la\s+)?(?:entrega|recepcion de la mercancia)|\bfecha de entrega"
)
_RECEIPT = re.compile(
    r"\b(?:up)?on receipt\b|\bdue immediately\b|\bpayable immediately\b|\bsofort\b"
    r"|\b(?:a|des) reception\b|\bbij ontvangst\b|\bal ricevimento\b|\ba la recepcion\b"
)
_DISCOUNT_NET = re.compile(r"\d+(?:\.\d+)?\s*%?\s*/\s*\d{1,3}\s*,?\s*n(?:et)?\s*/?\s*(\d{1,3})\b(?![.,]\d)")
_DAY = r"(?:days?|tagen?|jours?|dagen|giorni|gg\.?|dias?)"
_NET_WORD = r"(?:net|netto|neto|netti)"
_NET_FIRST = re.compile(r"\b" + _NET_WORD + r"\s*(?:a\s+)?/?\s*(\d{1,3})\b(?![.,]\d)")
_DAYS_FIRST = re.compile(r"\b(\d{1,3})\s*" + _DAY + r"\b(\s+" + _NET_WORD + r"\b)?")
_DISCOUNT = re.compile(r"%|skonto|escompte|korting|sconto|descuento|discount")
# Two periods offered together ("30 or 60 days", "30-60 Tage") name no single one.
_RANGE = re.compile(r"\b\d{1,3}\s*(?:or|oder|ou|of|o|y|to|bis|-|/)\s*\d{1,3}\s*" + _DAY + r"\b")
_PAYMENT_WORD = re.compile(
    r"\b(?:" + _NET_WORD + r"|pay|payment|payable|due|terms|zahlbar|zahlung|zahlungsziel|paiement|echeance|reglement"
    r"|betaling|betaalbaar|pagamento|pagabile|scadenza|pago|vencimiento|plazo)\b"
)


def plain_lower(text: str) -> str:
    import unicodedata

    # OCR reads the ordinal in "N.º" as a degree sign; both fold to "o".
    folded = unicodedata.normalize("NFKD", squashed(text).lower().replace("\u00b0", "o"))
    return "".join(c for c in folded if not unicodedata.combining(c))


def terms_days(text, alone: bool = False) -> int | str | None:
    """Days from the invoice date to the due date under printed terms.

    One payment period in days, 1 to 180, in English, French, German, Dutch,
    Italian, or Spanish ("Net 30", "30 days net", "zahlbar innerhalb 14 Tagen",
    "à 45 jours", "netto 30 dagen", "a 30 giorni", "neto 60 días") gives that
    many days. Payment on receipt gives 0. Discount terms give the net period.
    Terms the rule will not compute give UNCOMPUTABLE, which leaves the due
    date for a person: end-of-month or following-month terms, a period counted
    from anything but the invoice date, a discount period alone, two periods
    with nothing marking the net one, or more than 180 days. Anything else is
    not terms, and gives None.

    alone is for a line of page text with no label: it counts only when it also
    carries a payment word, so "Warranty: 90 days" is not terms."""
    if blank(text):
        return None
    lowered = plain_lower(text)
    if alone and not (_PAYMENT_WORD.search(lowered) or _RECEIPT.search(lowered) or _EOM.search(lowered)):
        return None
    if _EOM.search(lowered) or _ANCHOR_ELSEWHERE.search(lowered):
        return UNCOMPUTABLE
    if _RECEIPT.search(lowered):
        return 0
    if _RANGE.search(lowered):
        return UNCOMPUTABLE
    slashed = _DISCOUNT_NET.search(lowered)
    if slashed:
        return _within(int(slashed.group(1)))
    periods = {}
    for segment in re.split(r";|,(?!\d)", lowered):
        discount = bool(_DISCOUNT.search(segment))
        for found in _NET_FIRST.finditer(segment):
            periods[(segment, found.start(1))] = (int(found.group(1)), True, discount)
        for found in _DAYS_FIRST.finditer(segment):
            key = (segment, found.start(1))
            net = bool(found.group(2)) or periods.get(key, (0, False, False))[1]
            periods[key] = (int(found.group(1)), net, discount)
    if not periods:
        return None
    marked = {days for days, net, _ in periods.values() if net}
    plain = {days for days, net, discount in periods.values() if not net and not discount}
    choice = marked or plain
    if len(choice) != 1:
        return UNCOMPUTABLE
    return _within(choice.pop())


def _within(days: int) -> int | str:
    return days if 1 <= days <= TERMS_MAX_DAYS else UNCOMPUTABLE


def due_from_terms(invoice_date, due_raw, due_value, terms_raw):
    """The due date after printed terms. A printed due date wins, even one that
    does not parse. Terms printed where the due date goes ("Due Date: Upon
    receipt") count as terms."""
    if isinstance(due_value, date):
        return due_value
    days = terms_days(due_raw if due_value == "unparsed" else terms_raw)
    if days is None:
        return due_value
    if days == UNCOMPUTABLE or not isinstance(invoice_date, date):
        return None
    return invoice_date + timedelta(days=days)


def parsed(raw: dict) -> dict:
    out = {}
    for name in TEXT_FIELDS:
        out[name] = as_text(raw.get(name))
    out["currency"] = page_currency(raw)
    comma_decimal = out["currency"] in COMMA_DECIMAL_CURRENCIES
    # A supplier uses one convention on a page. A date that can only be one
    # order decides an ambiguous one beside it; failing that, a currency other
    # than the US dollar, or an amount written with a decimal comma, reads it
    # day-first.
    orders = {numeric_order(as_text(raw.get(name))) for name in DATE_FIELDS}
    if "day" in orders:
        day_first = True
    elif "month" in orders:
        day_first = False
    else:
        day_first = out["currency"] not in (None, "USD") or any(
            decimal_comma_shown(raw.get(name)) for name in MONEY_FIELDS + ("amount_due",)
        )
    for name in DATE_FIELDS:
        out[name] = as_date(raw.get(name), day_first)
    for name in MONEY_FIELDS:
        out[name] = as_money(raw.get(name), comma_decimal)
    if out["total"] is None:
        out["total"] = as_money(raw.get("amount_due"), comma_decimal)
    out["due_date"] = due_from_terms(out["invoice_date"], raw.get("due_date"), out["due_date"], raw.get("terms"))
    return out


def exception_reasons(values: dict) -> list[str]:
    reasons = [] if values.get("currency") in (None, "USD") else ["foreign_currency"]
    for name in FIELDS:
        if values[name] is None:
            reasons.append(f"missing_{name}")
    for name in DATE_FIELDS + MONEY_FIELDS:
        if values[name] == "unparsed":
            reasons.append(f"unparsed_{name}")
    invoice_date = values["invoice_date"]
    due_date = values["due_date"]
    if isinstance(invoice_date, date) and isinstance(due_date, date) and due_date < invoice_date:
        reasons.append("due_before_invoice")
    amounts = [values[name] for name in MONEY_FIELDS]
    if all(isinstance(amount, Decimal) for amount in amounts):
        subtotal, tax, total = amounts
        if abs(subtotal + tax - total) > CENT:
            reasons.append("total_mismatch")
    return reasons


def printed_text(raw: dict | None, values: dict) -> dict:
    """The printed text of each date or amount that did not parse, so a person,
    and the next scoring run, can see what was on the page. A total that came
    from the amount due shows the amount due's text. Empty when nothing failed."""
    if not raw:
        return {}
    out = {}
    for name in DATE_FIELDS + MONEY_FIELDS:
        if values[name] != "unparsed":
            continue
        source = raw.get(name)
        if name == "total" and blank(source):
            source = raw.get("amount_due")
        out[name] = as_text(source)
    return out


def show(value) -> str | None:
    if value is None or value == "unparsed":
        return None if value is None else "unparsed"
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    return str(value)


def squashed(value) -> str:
    return " ".join(str(value).split())


def money_label_words(text: str) -> list[str] | None:
    words = re.findall(r"[A-Za-z]+", text)
    if not words:
        return None
    keys = [word.lower() for word in words]
    if any(key not in MONEY_LABELS for key in keys):
        return None
    return keys


def money_amounts(text: str) -> list[str]:
    """Amounts in a line, with any currency sign kept: "$1,234.56", "1.234,56 €", "£980.00"."""
    return re.findall(
        r"(?:(?:US|CA|C)?\$|€|£)?\s?\d[\d.,\u00a0\u202f ]*?[.,]\d{2}(?!\d)(?:\s?(?:€|£|EUR|GBP|CAD|USD))?", text
    )


BARE_LABELS = (
    {prefix for _name, prefix in FIELD_PREFIXES}
    | {prefix for _name, prefix in TERMS_PREFIXES}
    | set(AMOUNT_DUE_PREFIXES)
    | set(MONEY_LABELS)
    | HEADER_SKIP
)


def bare_label(text: str) -> bool:
    return text.lower().strip(" :.-") in BARE_LABELS


def take_labeled(fields: dict, text: str, following: str | None) -> None:
    lowered = text.lower().rstrip()
    for name, prefix in FIELD_PREFIXES:
        if fields[name] is not None:
            continue
        if lowered == prefix and following and not bare_label(following):
            fields[name] = following
            continue
        if lowered.startswith(prefix):
            rest = text[len(prefix) :].strip(" :.-")
            if rest and not bare_label(rest):
                fields[name] = rest


def take_money(fields: dict, text: str, following: str | None) -> None:
    labels = money_label_words(text)
    if not labels:
        return
    amounts = money_amounts(text)
    if len(amounts) < len(labels) and following and not bare_label(following):
        amounts = money_amounts(following)
    for label, amount in zip(labels, amounts):
        if fields[label] is None:
            fields[label] = amount


def take_terms(fields: dict, text: str, following: str | None) -> None:
    """Printed payment terms: after a "Terms" label, or a short line that is
    terms on its own ("Due on receipt", "Net 30")."""
    if fields["terms"] is not None:
        return
    lowered = plain_lower(text)
    for _name, prefix in TERMS_PREFIXES:
        if lowered.strip(" :.-") == prefix:
            candidate = following if following and not bare_label(following) else None
        elif lowered.startswith(prefix):
            candidate = text[len(prefix) :].strip(" :.-")
        else:
            continue
        if candidate and terms_days(candidate) is not None:
            fields["terms"] = candidate
            return
    if len(text) <= TERMS_LINE_MAX and terms_days(text, alone=True) is not None:
        fields["terms"] = text


def take_amount_due(fields: dict, text: str, following: str | None) -> None:
    if fields["amount_due"] is not None:
        return
    lowered = text.lower()
    if not any(lowered.startswith(prefix) for prefix in AMOUNT_DUE_PREFIXES):
        return
    amounts = money_amounts(text)
    if not amounts and following and not bare_label(following):
        amounts = money_amounts(following)
    if amounts:
        fields["amount_due"] = amounts[0]


def take_all(fields: dict, text: str, following: str | None) -> None:
    take_labeled(fields, text, following)
    take_money(fields, text, following)
    take_terms(fields, text, following)
    take_amount_due(fields, text, following)


def take_row(fields: dict, cells: dict) -> None:
    ordered = sorted(cells.items())
    for index, (_col, text) in enumerate(ordered):
        following = ordered[index + 1][1] if index + 1 < len(ordered) else None
        take_all(fields, text, following)


def fields_from_docling(doc: dict) -> dict:
    fields = {name: None for name in RAW_KEYS}
    texts = []
    for item in doc.get("texts") or []:
        text = squashed(item.get("text") or "")
        if not text:
            continue
        texts.append(text)
        if fields["supplier"] is None and item.get("label") == "section_header":
            if text.lower() not in HEADER_SKIP:
                fields["supplier"] = text
    for index, text in enumerate(texts):
        following = texts[index + 1] if index + 1 < len(texts) else None
        take_all(fields, text, following)
    for table in doc.get("tables") or []:
        rows: dict[int, dict[int, str]] = {}
        for cell in (table.get("data") or {}).get("table_cells") or []:
            text = squashed(cell.get("text") or "")
            if not text:
                continue
            row = cell.get("start_row_offset_idx")
            col = cell.get("start_col_offset_idx")
            if not isinstance(row, int) or not isinstance(col, int):
                continue
            rows.setdefault(row, {})[col] = text
        for cells in rows.values():
            take_row(fields, cells)
    return fields


def docling_read(pdf: Path) -> tuple[dict | None, str, str | None]:
    boundary = f"iap{uuid.uuid4().hex}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="files"; filename="{pdf.name}"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode() + pdf.read_bytes() + (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="to_formats"\r\n\r\n'
        f"json\r\n--{boundary}--\r\n"
    ).encode()
    req = urllib.request.Request(
        DOCLING_URL,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=DOCLING_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except TimeoutError:
        return None, "docling", f"timed out after {DOCLING_TIMEOUT}s"
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        return None, "docling", f"docling request failed: {reason}"
    if not isinstance(payload, dict):
        return None, "docling", "docling output was not JSON"
    if payload.get("status") != "success":
        errors = payload.get("errors")
        return None, "docling", f"docling status {payload.get('status')}: {errors}"
    document = payload.get("document") if isinstance(payload.get("document"), dict) else {}
    content = document.get("json_content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            return None, "docling", "docling document was not JSON"
    if not isinstance(content, dict):
        return None, "docling", "docling returned no document"
    return fields_from_docling(content), "docling", None


def textract_fields(response: dict) -> dict:
    """Map AnalyzeExpense summary fields to the seven, plus the amount due and
    the payment terms. For each, the detection with the highest value
    confidence wins. When no PAYMENT_TERMS came back, terms Textract leaves
    untyped count: an OTHER field under a label with "terms" in it, then a
    short line of page text that is terms on its own ("Due on receipt")."""
    best: dict[str, tuple[float, str]] = {}
    loose_terms: tuple[float, str] | None = None
    terms_line: str | None = None
    lines: list[tuple[str, dict]] = []
    currency: tuple[float, str] | None = None
    currency_field: str | None = None
    for document in response.get("ExpenseDocuments") or []:
        for block in document.get("Blocks") or []:
            text = block.get("Text")
            if block.get("BlockType") == "LINE" and isinstance(text, str):
                lines.append((text, (block.get("Geometry") or {}).get("BoundingBox") or {}))
            if terms_line is None and block.get("BlockType") == "LINE" and isinstance(text, str):
                if len(text) <= TERMS_LINE_MAX and terms_days(text, alone=True) is not None:
                    terms_line = text
        for field in document.get("SummaryFields") or []:
            kind = (field.get("Type") or {}).get("Text")
            name = TEXTRACT_TYPES.get(kind)
            detection = field.get("ValueDetection") or {}
            value = detection.get("Text")
            if not isinstance(value, str) or blank(value):
                continue
            confidence = float(detection.get("Confidence") or 0)
            label_text = (field.get("LabelDetection") or {}).get("Text") or ""
            code = (field.get("Currency") or {}).get("Code")
            if kind in ("SUBTOTAL", "TAX", "TOTAL", "AMOUNT_DUE") and code in CURRENCIES:
                score = float((field.get("Currency") or {}).get("Confidence") or 0)
                if currency is None or score > currency[0]:
                    currency = (score, code)
            if kind == "OTHER" and plain_lower(label_text).strip(" :.-") in ("currency", "wahrung", "devise", "valuta", "moneda"):
                currency_field = value.strip().upper() if value.strip().upper() in CURRENCIES else currency_in(value)
            if is_due_label(label_text) and kind in ("INVOICE_RECEIPT_DATE", "OTHER", "DUE_DATE"):
                name = "due_date"
            elif kind == "OTHER":
                folded_label = plain_lower(label_text).strip(" :.-")
                name = next((field for field, labels in LABELS_FOR.items()
                             if field != "due_date" and folded_label in labels), None)
            if name is None:
                label = ((field.get("LabelDetection") or {}).get("Text") or "").lower()
                if kind == "OTHER" and any(word in plain_lower(label) for word in TERMS_LABEL_WORDS) and terms_days(value) is not None:
                    if loose_terms is None or confidence > loose_terms[0]:
                        loose_terms = (confidence, value)
                continue
            if name not in best or confidence > best[name][0]:
                best[name] = (confidence, value)
    if "terms" not in best and loose_terms is not None:
        best["terms"] = loose_terms
    if "terms" not in best and terms_line is not None:
        best["terms"] = (0.0, terms_line)
    if currency is not None:
        best["currency"] = currency
    elif currency_field is not None:
        best["currency"] = (0.0, currency_field)
    for name, labels in LABELS_FOR.items():
        if name not in best:
            beside = value_beside_label(lines, labels)
            if beside is not None:
                best[name] = (0.0, beside)
    return {name: best[name][1] if name in best else None for name in RAW_KEYS}


def is_due_label(label: str) -> bool:
    return plain_lower(label).strip(" :.-") in DUE_LABELS


def value_beside_label(lines: list[tuple[str, dict]], labels: tuple[str, ...]) -> str | None:
    """The value printed with one of these labels on the page: after it on the
    same line, or the nearest line to its right on the same row."""
    folded_labels = sorted({plain_lower(label).strip(" :.-") for label in labels}, key=len, reverse=True)
    for text, box in lines:
        folded = plain_lower(text)
        label = next((l for l in folded_labels
                      if folded.rstrip(" :.-") == l or folded.startswith(l + " ") or folded.startswith(l + ":")), None)
        if label is None:
            continue
        rest = folded[len(label):].strip(" :.-")
        if rest:
            return squashed(text)[len(label):].strip(" :.-")
        top, left = box.get("Top"), box.get("Left")
        if top is None or left is None:
            continue
        row = [(b.get("Left", 0), t) for t, b in lines
               if b.get("Top") is not None and abs(b["Top"] - top) < 0.01 and b.get("Left", 0) > left]
        if row:
            return min(row)[1]
    return None


_CLIENTS: dict[tuple, object] = {}


def textract_client(profile: str | None = None, region: str | None = None):
    """One client per profile and region for the life of the process.
    Region falls back to AWS_REGION (set inside Lambda), then us-east-1."""
    region = region or os.environ.get("AWS_REGION") or DEFAULT_REGION
    key = (profile, region)
    if key not in _CLIENTS:
        import boto3

        _CLIENTS[key] = boto3.Session(profile_name=profile, region_name=region).client("textract")
    return _CLIENTS[key]


def textract_read(pdf: Path, client=None, profile: str | None = None) -> tuple[dict | None, str, str | None, int]:
    """Returns (fields, runner, error, pages). A failure is the error, not a raise."""
    try:
        client = client or textract_client(profile)
        response = client.analyze_expense(Document={"Bytes": pdf.read_bytes()})
    except ImportError:
        return None, TEXTRACT_MODEL, "textract needs boto3: pip install boto3", 0
    except Exception as exc:
        return None, TEXTRACT_MODEL, f"textract request failed: {exc}", 0
    pages = int((response.get("DocumentMetadata") or {}).get("Pages") or 0)
    return textract_fields(response), TEXTRACT_MODEL, None, pages


def read_fields(
    pdf: Path, runner: str = DEFAULT_RUNNER, client=None, profile: str | None = None
) -> tuple[dict | None, str, str | None, int]:
    """The raw seven from one runner: (fields, runner, error, pages)."""
    if runner == "textract":
        return textract_read(pdf, client, profile)
    if runner == "docling":
        got_raw, used, error = docling_read(pdf)
        return got_raw, used, error, 0
    raise ValueError(f"unknown runner {runner}")


def read_invoice(
    pdf: Path, runner: str = DEFAULT_RUNNER, client=None, profile: str | None = None
) -> dict:
    got_raw, used, error, pages = read_fields(pdf, runner, client, profile)
    values = {name: None for name in FIELDS} if got_raw is None else parsed(got_raw)
    reasons = exception_reasons(values)
    out = {name: show(values[name]) for name in FIELDS}
    out["exception"] = bool(reasons)
    out["exception_reasons"] = reasons
    out["currency"] = values.get("currency")
    out["printed"] = printed_text(got_raw, values)
    out["error"] = error
    out["runner"] = used
    out["pages"] = pages
    return out


def make_record(pdf: Path, result: dict, seconds: float) -> dict:
    record = {
        "file_name": pdf.name,
        "id": uuid.uuid4().hex,
        "read_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(seconds, 1),
        "runner": result.get("runner"),
    }
    for name in FIELDS:
        record[name] = result[name]
    record["exception"] = result["exception"]
    record["exception_reasons"] = list(result["exception_reasons"])
    record["printed"] = dict(result.get("printed") or {})
    record["error"] = result["error"]
    return record


def append_record(record: dict) -> None:
    READS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with READS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Read one supplier invoice.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--runner", choices=RUNNERS, default=DEFAULT_RUNNER)
    parser.add_argument("--profile", help="AWS profile for the textract runner.")
    args = parser.parse_args()
    pdf = args.pdf
    if not pdf.is_file():
        raise SystemExit(f"no file {pdf}")
    started = time.perf_counter()
    result = read_invoice(pdf, args.runner, profile=args.profile)
    record = make_record(pdf, result, time.perf_counter() - started)
    append_record(record)
    print(json.dumps(result, indent=2))
    if result["error"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
