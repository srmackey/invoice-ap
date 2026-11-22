#!/usr/bin/env python3
"""
make_set.py - generate a labeled supplier-invoice test set (holdout-2).

One run writes labels.json and the PDFs in clean/, scans/ and photos/.
Every value on a page is decided here first; the page is drawn from those
values and the label is derived from the same values by the rules in the
brief. Nothing is hand-edited.

    python make_set.py            # seed 0
    python make_set.py --seed 0
"""
import argparse
import hashlib
import io
import json
import math
import random
import re
import shutil
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import cv2
import numpy as np
import pymupdf  # used only to rasterize our own clean PDFs into scan/photo images
import reportlab
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent
D = Decimal
LETTER = (612.0, 792.0)
FIELDS = ["supplier", "invoice_number", "invoice_date", "due_date", "subtotal", "tax", "total"]
RENDER_DIR = {"clean": "clean", "scan": "scans", "photo": "photos"}
WINDOW = (date(2024, 12, 2), date(2026, 11, 13))


def q2(x):
    return D(x).quantize(D("0.01"), ROUND_HALF_UP)


def s2(x):
    return None if x is None else f"{q2(x):.2f}"


# ----------------------------------------------------------------------------- fonts
FONTDIR = Path(reportlab.__file__).parent / "fonts"


def register_fonts():
    for name, f in [("Vera", "Vera.ttf"), ("Vera-Bold", "VeraBd.ttf"),
                    ("Vera-Italic", "VeraIt.ttf"), ("Vera-BoldItalic", "VeraBI.ttf")]:
        pdfmetrics.registerFont(TTFont(name, str(FONTDIR / f)))


FONTS = {
    "helv": dict(r="Helvetica", b="Helvetica-Bold", i="Helvetica-Oblique"),
    "times": dict(r="Times-Roman", b="Times-Bold", i="Times-Italic"),
    "courier": dict(r="Courier", b="Courier-Bold", i="Courier-Oblique"),
    "vera": dict(r="Vera", b="Vera-Bold", i="Vera-Italic"),
}

# ----------------------------------------------------------------------------- formats
MON = ["January", "February", "March", "April", "May", "June", "July", "August",
       "September", "October", "November", "December"]


def fmt_date(d, style):
    m, dd, y = d.month, d.day, d.year
    return {
        "mdy": f"{m:02d}/{dd:02d}/{y}",
        "mdy_np": f"{m}/{dd}/{y}",
        "mdy2": f"{m:02d}/{dd:02d}/{y % 100:02d}",
        "long": f"{MON[m - 1]} {dd}, {y}",
        "abbr": f"{MON[m - 1][:3]} {dd}, {y}",
        "dmon": f"{dd:02d}-{MON[m - 1][:3]}-{y}",
        "dmon2": f"{dd:02d}-{MON[m - 1][:3].upper()}-{y % 100:02d}",
        "iso": f"{y}-{m:02d}-{dd:02d}",
        "dlong": f"{dd} {MON[m - 1]} {y}",
        "mdy_dash": f"{m:02d}-{dd:02d}-{y}",
    }[style]


def fmt_money(v, style, neg="minus"):
    v = q2(v)
    a = abs(v)
    body = f"{a:.2f}" if style == "nocomma" else f"{a:,.2f}"
    s = {"dollar": "$" + body, "dollar_sp": "$ " + body}.get(style, body)
    if v < 0:
        s = f"({s})" if neg == "paren" else "-" + s
    return s


# ----------------------------------------------------------------------------- the customer
CUSTOMER = dict(
    name="Owlsedge Brewing Co.",
    legal="Owlsedge Brewing Company LLC",
    attn="Attn: Accounts Payable",
    addr=["2207 Quillan Mill Rd", "Fenmore, CO 80947"],
    phone="(719) 555-0148",
    sites=[("Owlsedge Taproom", "2207 Quillan Mill Rd", "Fenmore, CO 80947"),
           ("Owlsedge Production", "58 Teasel Industrial Ct, Unit C", "Fenmore, CO 80949")],
)


# ----------------------------------------------------------------------------- suppliers
def I(sku, desc, unit, plo, phi, qlo, qhi, tax=True, kind="part", step=1):
    return dict(sku=sku, desc=desc, unit=unit, plo=plo, phi=phi, qlo=qlo, qhi=qhi,
                tax=tax, kind=kind, step=step)


def F(desc, qty, unit, price, tax=False, sku="", kind="svc"):
    return dict(sku=sku, desc=desc, unit=unit, price=price, qty=qty, tax=tax, kind=kind, fixed=True)


BASE_LABELS = dict(num="Invoice #", date="Invoice Date", due="Due Date", terms="Terms", sub="Subtotal",
                   tax="Sales Tax", total="Total", po="P.O. #", acct="Account #",
                   paid="Payments/Credits", balance="Balance Due")

SUPPLIERS = [
    # --- utility-style monthly / quarterly bills with a remittance stub
    dict(key="grindstone", slug="grindstone-disposal", name="Grindstone Valley Disposal",
         legal="Grindstone Valley Disposal, Inc.", layout="utility_stub", n=6, cadence="monthly", bill_day=1,
         addr=["PO Box 4410", "Fenmore, CO 80946"], phone="(719) 555-0172", web="grindstonevalleydisposal.net",
         logo="mountain", color=(0.16, 0.42, 0.22), font="helv",
         num=lambda s, d: f"{s:07d}", seq0=2184410, rate=(38, 52),
         date_fmt="mdy", money="dollar", terms=None, due_mode="date", due_days=20,
         labels=dict(num="Invoice Number", due="Payment Due Date", total="Invoice Total", tax="Tax"),
         tax=None, total_style="total", acct="40-118273",
         fixed=[F("Front-load 4 yd container, 2x weekly", 1, "MO", "312.00"),
                F("Single-stream recycling, 96 gal cart x3", 1, "MO", "27.00")],
         extra_items=[I("XP4", "Extra pickup - 4 yd container", "EA", 68, 68, 1, 2, tax=False, kind="svc"),
                      I("OVR", "Container overage / lid not closed", "EA", 35, 35, 1, 1, tax=False, kind="svc")],
         extra_p=0.35, surcharge=("Fuel/environmental recovery fee", D("0.125")),
         channel=(0.4, 0.5, 0.1),
         notes=["Holiday schedule: no service Dec 25 or Jan 1. Service will run the following day.",
                "Questions about your bill? Call customer care Mon-Fri 7am-4pm."]),
    dict(key="sentrel", slug="sentrel-alarm", name="Sentrel Alarm Monitoring",
         layout="utility_stub", n=3, cadence="quarterly", bill_day=15,
         addr=["1150 Osprey Bend Pkwy, Ste 210", "Calder Springs, CO 80911"], phone="(719) 555-0119",
         web="sentrelalarm.com", logo="shield", color=(0.12, 0.22, 0.48), font="vera",
         num=lambda s, d: f"SAM-{s:06d}", seq0=310221, rate=(55, 70),
         date_fmt="abbr", money="dollar", terms="Net 30", due_mode="date",
         labels=dict(num="Invoice No.", date="Statement Date", due="Due Date", total="Total This Invoice"),
         tax=D("0.0765"), total_style="total", acct="SM-77031",
         fixed=[F("Central station monitoring - burglary & fire (3 mo)", 3, "MO", "42.95", tax=True),
                F("Cellular communicator service (3 mo)", 3, "MO", "7.00", tax=True)],
         channel=(0.7, 0.2, 0.1),
         notes=["Please update your emergency call list by calling the number above.",
                "Monitoring is billed quarterly in advance."]),
    dict(key="hearthstone", slug="hearthstone-propane", name="Hearthstone Propane",
         legal="Hearthstone Propane & Fuel LLC", layout="utility_stub", n=2,
         addr=["7809 County Road 14", "Ashby Flats, CO 80928"], phone="(719) 555-0163",
         web="hearthstonepropane.com", logo="flame", color=(0.72, 0.30, 0.08), font="helv",
         num=lambda s, d: f"HP{s:06d}", seq0=88120, rate=(10, 16),
         date_fmt="mdy", money="dollar", terms="Net 10", due_mode="date",
         labels=dict(num="Ticket/Invoice", due="Due Date", total="Invoice Total"),
         tax=D("0.0290"), total_style="total", acct="HP-20417",
         items=[I("FLC33", "Forklift cylinder exchange, 33.5 lb", "EA", 31.75, 31.75, 4, 10, kind="part"),
                I("HAZ", "Hazmat / delivery fee", "EA", 14.50, 14.50, 1, 1, kind="fee"),
                I("CYLR", "Cylinder cage rental", "MO", 12.00, 12.00, 1, 1, kind="fee")],
         nitems=(2, 3), channel=(0.3, 0.4, 0.3),
         notes=["Cylinders remain property of Hearthstone Propane.", "Keep cage locked when unattended."]),

    # --- dot-matrix / continuous-form distributors
    dict(key="tallowmere", slug="tallowmere-linen", name="TALLOWMERE LINEN & UNIFORM",
         legal="Tallowmere Linen Services, Inc.", layout="dotmatrix", n=6, cadence="weekly",
         addr=["3340 W. Brasher Ave", "Calder Springs, CO 80907"], phone="(719) 555-0185",
         web=None, logo="hex", color=(0.10, 0.35, 0.55), font="courier", greenbar=True,
         num=lambda s, d: f"{s:08d}", seq0=40120880, rate=(140, 190),
         date_fmt="mdy2", money="plain", terms="NET 15", due_mode="none",
         labels=dict(num="INVOICE NO", date="INVOICE DATE", terms="TERMS", sub="SUBTOTAL", tax="SALES TAX",
                     total="TOTAL", po="CUST PO", acct="CUST NO"),
         tax=D("0.0765"), total_style="total", acct="0071834",
         items=[I("1102", "SHOP TOWEL RED", "EA", .14, .14, 100, 200, step=25),
                I("1140", "BAR MOP WHITE", "EA", .19, .19, 50, 150, step=25),
                I("2210", "APRON BIB BLACK", "EA", .62, .62, 8, 16),
                I("3305", "FLOOR MAT 3X5 GRAY", "EA", 3.40, 3.40, 3, 5),
                I("3346", "FLOOR MAT 4X6 LOGO", "EA", 6.85, 6.85, 1, 2),
                I("9001", "ROUTE SERVICE CHARGE", "EA", 9.95, 9.95, 1, 1),
                I("9010", "ENERGY SURCHARGE", "EA", 4.50, 4.50, 1, 1)],
         nitems=(4, 7), always=["9001"], channel=(0.2, 0.6, 0.2),
         notes=["LOST OR DAMAGED ITEMS BILLED AT REPLACEMENT COST.",
                "1.5% PER MONTH SERVICE CHARGE ON PAST DUE BALANCES."]),
    dict(key="redtail", slug="redtail-freight", name="REDTAIL FREIGHT LINES",
         layout="dotmatrix", n=3,
         addr=["900 Terminal Loop Rd", "Pueblo West, CO 81007"], phone="(719) 555-0130",
         web="redtailfreightlines.com", logo="bars", color=(0.62, 0.12, 0.10), font="courier",
         num=lambda s, d: f"{s // 1000}-{s % 1000:03d}", seq0=7731204, rate=(400, 600),
         date_fmt="mdy", money="plain", terms="Net 15", due_mode="date",
         labels=dict(num="FREIGHT BILL / INVOICE NO", date="BILL DATE", due="DUE DATE", terms="TERMS",
                     sub="SUBTOTAL", tax="TAX", total="TOTAL CHARGES", po="SHIPPER REF", acct="ACCT"),
         tax=None, total_style="total", acct="OWL001",
         items=[I("LH", "LINEHAUL", "CWT", 38.0, 38.0, 6, 22, tax=False, kind="svc"),
                I("LG", "LIFTGATE DELIVERY", "EA", 85.0, 85.0, 1, 1, tax=False, kind="svc"),
                I("LA", "LIMITED ACCESS DELIVERY", "EA", 65.0, 65.0, 1, 1, tax=False, kind="svc"),
                I("IN", "INSIDE DELIVERY", "EA", 95.0, 95.0, 1, 1, tax=False, kind="svc")],
         nitems=(2, 3), always=["LH"], surcharge=("FUEL SURCHARGE", D("0.315")), freight=True,
         channel=(0.5, 0.4, 0.1), notes=["CLAIMS MUST BE FILED WITHIN 9 MONTHS OF DELIVERY.",
                                          "REMIT WITH FREIGHT BILL NUMBER."]),
    dict(key="sweetgrass", slug="sweetgrass-provisions", name="Sweetgrass Provisions Wholesale",
         layout="dotmatrix", n=4,
         addr=["14 Granary Row", "Calder Springs, CO 80903"], phone="(719) 555-0107",
         web=None, logo="barley", color=(0.45, 0.33, 0.10), font="courier",
         num=lambda s, d: f"S{s:07d}", seq0=1204550, rate=(60, 90),
         date_fmt="mdy", money="plain", terms="Net 7", due_mode="none",
         labels=dict(num="INVOICE #", date="DATE", terms="TERMS", sub="SUBTOTAL", tax="TAX",
                     total="INVOICE TOTAL", po="PO#", acct="CUST#"),
         tax=D("0.0765"), total_style="total", acct="10244", bo=True,
         items=[I("44120", "PRETZEL TWIST SOFT 6OZ 1/48", "CS", 58.40, 58.40, 1, 3, tax=False),
                I("44188", "BEER CHEESE DIP 4/5LB", "CS", 71.25, 71.25, 1, 2, tax=False),
                I("51002", "PEANUTS ROASTED SALTED 25LB", "BG", 46.90, 46.90, 1, 2, tax=False),
                I("51040", "SNACK MIX BAR 2/5LB", "CS", 39.80, 39.80, 1, 3, tax=False),
                I("70310", "CUP CLEAR PET 16OZ 1000CT", "CS", 64.15, 64.15, 1, 2),
                I("70330", "NAPKIN BEV WHITE 4000CT", "CS", 28.60, 28.60, 1, 2),
                I("70404", "BOAT FOOD TRAY #100 1000CT", "CS", 33.75, 33.75, 1, 1)],
         nitems=(3, 6), channel=(0.3, 0.4, 0.3),
         notes=["PERISHABLE CLAIMS MUST BE MADE AT TIME OF DELIVERY."]),

    # --- QuickBooks-style templates
    dict(key="pellham", slug="pellham-gas", name="Pellham Gas & Welding Supply",
         layout="quickbooks", n=4, qb_due=True,
         addr=["2611 Foundry St", "Calder Springs, CO 80905"], phone="(719) 555-0151",
         web=None, logo="circle", color=(0.20, 0.20, 0.20), font="helv",
         num=lambda s, d: f"{s}", seq0=31877, rate=(4, 7),
         date_fmt="mdy_np", money="plain", terms="Net 30", due_mode="date",
         tax=D("0.0765"), total_style="total", acct=None,
         items=[I("CO2-50", "CO2 beverage grade 50 lb - refill", "EA", 46.00, 46.00, 2, 6),
                I("N2-300", "Nitrogen 300 cf - refill", "EA", 39.50, 39.50, 1, 3),
                I("BG-75", "Beer gas 75/25 - 20 lb", "EA", 42.25, 42.25, 1, 2),
                I("RNT", "Cylinder rental (monthly)", "EA", 6.50, 6.50, 4, 8),
                I("DLV", "Delivery", "EA", 15.00, 15.00, 1, 1)],
         nitems=(2, 4), always=["RNT"], channel=(0.5, 0.3, 0.2), notes=["Thank you for your business."]),
    dict(key="driftmoor", slug="driftmoor-packaging", name="Driftmoor Packaging Supply",
         layout="quickbooks", n=5, qb_due=False,
         addr=["600 Wexley Ave, Bldg 3", "Denver, CO 80216"], phone="(303) 555-0122",
         web="driftmoorpack.com", logo="diamond", color=(0.05, 0.40, 0.45), font="helv",
         num=lambda s, d: f"{s}", seq0=12040, rate=(6, 9),
         date_fmt="mdy", money="dollar", terms="Net 30", due_mode="none",
         tax=D("0.0765"), total_style="total_balance", acct=None,
         items=[I("CN16-BR", "16 oz can, bright, 1 layer (408 ct)", "LYR", 98.00, 98.00, 4, 12),
                I("END202", "202 can end, silver (case 2,000)", "CS", 61.50, 61.50, 1, 3),
                I("PC4-BK", "4-pack can carrier, black (case 1,000)", "CS", 112.00, 112.00, 1, 2),
                I("TRY24", "24-can tray, kraft (bundle 50)", "BDL", 47.25, 47.25, 1, 4),
                I("FRT", "Freight", "EA", 85.00, 160.00, 1, 1, tax=False, kind="fee")],
         nitems=(3, 5), always=["FRT"], channel=(0.6, 0.3, 0.1),
         notes=["Claims for shortage or damage must be made within 5 days of receipt."]),
    dict(key="clearbrook", slug="clearbrook-janitorial", name="Clearbrook Janitorial Supply",
         layout="quickbooks", n=3, qb_due=True,
         addr=["88 Larrow Ct", "Calder Springs, CO 80910"], phone="(719) 555-0194",
         web="clearbrookjan.com", logo="drop", color=(0.15, 0.45, 0.70), font="helv",
         num=lambda s, d: f"CJ{s}", seq0=50112, rate=(9, 14),
         date_fmt="mdy", money="plain", terms="Net 15", due_mode="date",
         tax=D("0.0765"), total_style="total", acct=None,
         items=[I("CH-PAA", "Peracetic acid sanitizer 5 gal", "PL", 118.00, 118.00, 1, 2),
                I("CH-CIP", "Caustic CIP cleaner 5 gal", "PL", 96.50, 96.50, 1, 2),
                I("TT-2", "Toilet tissue 2-ply 96 rl", "CS", 74.20, 74.20, 1, 2),
                I("HT-8", "Hand towel roll 8\" brown 6/800'", "CS", 52.80, 52.80, 1, 3),
                I("LN-56", "Can liner 40x46 1.5 mil 100 ct", "CS", 44.10, 44.10, 1, 2),
                I("GL-NIT", "Nitrile gloves L 10/100", "CS", 63.90, 63.90, 1, 1)],
         nitems=(2, 5), channel=(0.5, 0.3, 0.2), notes=["Returns accepted within 30 days with invoice."]),
    dict(key="kettlebrook", slug="kettlebrook-stainless", name="Kettlebrook Stainless Fittings",
         layout="quickbooks", n=1, qb_due=False,
         addr=["4120 Commerce Dr", "Loveland, CO 80538"], phone="(970) 555-0146",
         web="kettlebrookss.com", logo="gear", color=(0.35, 0.38, 0.42), font="helv",
         num=lambda s, d: f"KSF-{s}", seq0=9310, rate=(2, 4),
         date_fmt="mdy", money="dollar", terms="Net 30 EOM", due_mode="none",
         tax=D("0.0765"), total_style="total", acct=None,
         items=[I("TC-150", "1.5\" tri-clamp, heavy duty", "EA", 14.80, 14.80, 4, 10),
                I("GSK-150", "1.5\" EPDM gasket", "EA", 1.35, 1.35, 10, 30, step=5),
                I("BV-150", "1.5\" TC butterfly valve", "EA", 68.00, 68.00, 1, 3),
                I("HSE-58", "5/8\" EPDM brewery hose, per ft", "FT", 9.40, 9.40, 10, 25, step=5)],
         nitems=(3, 4), channel=(0.7, 0.2, 0.1), notes=["All sales final on custom-cut hose."]),

    # --- banded modern
    dict(key="corvane", slug="corvane-malt", name="Corvane Malt & Grain",
         legal="Corvane Malt & Grain Co., LLC", layout="banded", n=6,
         addr=["2 Elevator Rd", "Hollister Junction, CO 80742"], phone="(970) 555-0177",
         web="corvanemalt.com", logo="barley", color=(0.55, 0.36, 0.12), font="vera",
         tagline="Craft malts, milled to order",
         num=lambda s, d: f"CM-{d.year}-{s:04d}", seq0=110, rate=(1.2, 1.8), yearly=True,
         date_fmt="abbr", money="dollar", terms="2/10 Net 30", due_mode="none",
         labels=dict(num="Invoice No.", date="Invoice Date", po="Your PO"),
         tax=None, total_style="total", acct="OWL-02",
         items=[I("2RW", "2-Row Pale Malt, 50 lb", "BAG", 41.50, 41.50, 10, 40, tax=False),
                I("MUN10", "Munich 10L, 50 lb", "BAG", 47.80, 47.80, 2, 8, tax=False),
                I("C40", "Caramel 40L, 50 lb", "BAG", 52.60, 52.60, 1, 4, tax=False),
                I("WHT", "White Wheat Malt, 50 lb", "BAG", 45.20, 45.20, 2, 6, tax=False),
                I("FLK-O", "Flaked Oats, 50 lb", "BAG", 49.90, 49.90, 1, 4, tax=False),
                I("MILL", "Milling charge", "BAG", 1.25, 1.25, 10, 40, tax=False, kind="fee"),
                I("FRT", "Freight - pallet delivery", "EA", 145.00, 145.00, 1, 1, tax=False, kind="fee")],
         nitems=(3, 6), always=["2RW", "FRT"], channel=(0.6, 0.2, 0.2),
         notes=["Malt is sold for brewing use; resale certificate on file.", "Lot numbers available on request."]),
    dict(key="fenwick", slug="fenwick-glass", name="Fenwick Glass & Promo",
         layout="banded", n=2, ship_after_sub=True,
         addr=["311 Saddler Way", "Boulder, CO 80301"], phone="(303) 555-0139",
         web="fenwickglasspromo.com", logo="sun", color=(0.48, 0.18, 0.45), font="helv",
         tagline="Custom glassware & branded merchandise",
         num=lambda s, d: f"FG{s}", seq0=20733, rate=(5, 8),
         date_fmt="long", money="dollar", terms="Net 15", due_mode="date",
         tax=D("0.0765"), total_style="total", acct=None,
         items=[I("PNT-16", "16 oz shaker pint, 1-color logo", "EA", 3.85, 3.85, 72, 288, step=72),
                I("TLP-13", "13 oz tulip, 2-color logo", "EA", 6.40, 6.40, 48, 144, step=24),
                I("SET", "Screen setup charge", "EA", 45.00, 45.00, 1, 1, kind="fee"),
                I("STK", "Die-cut vinyl sticker 3\"", "EA", .68, .68, 250, 500, step=250)],
         nitems=(2, 3), always=["SET"], channel=(0.8, 0.1, 0.1), notes=["Art proofs approved by customer."]),

    # --- minimal modern
    dict(key="ninebark", slug="ninebark-hops", name="Ninebark Hop Merchants",
         layout="minimal", n=4,
         addr=["19 Trellis Lane", "Paonia, CO 81428"], phone="(970) 555-0111",
         web="ninebarkhops.com", logo="leaf", color=(0.30, 0.50, 0.20), font="vera",
         num=lambda s, d: f"NB{d.year % 100:02d}{s:04d}", seq0=220, rate=(0.6, 1.0), yearly=True,
         date_fmt="dlong", money="dollar", terms="Net 30", due_mode="date",
         labels=dict(num="Invoice number", date="Date of issue", due="Due date", sub="Subtotal", tax="Tax",
                     total="Amount due"),
         tax=None, total_style="due", acct=None,
         items=[I("CIT-P", "Citra T-90 pellets, 11 lb", "BOX", 152.00, 152.00, 1, 4, tax=False),
                I("MOS-P", "Mosaic T-90 pellets, 11 lb", "BOX", 141.00, 141.00, 1, 4, tax=False),
                I("CAS-P", "Cascade T-90 pellets, 11 lb", "BOX", 88.00, 88.00, 1, 3, tax=False),
                I("SAZ-P", "Saaz T-90 pellets, 11 lb", "BOX", 96.00, 96.00, 1, 2, tax=False),
                I("SHIP", "Cold-chain shipping", "EA", 38.00, 38.00, 1, 1, tax=False, kind="fee")],
         nitems=(2, 4), always=["SHIP"], channel=(0.9, 0.05, 0.05),
         notes=["Pay online or by ACH. Contract volumes reserved through harvest."]),
    dict(key="quarrystone", slug="quarrystone-tech", name="Quarrystone Tech Partners",
         layout="minimal", n=5, cadence="monthly", bill_day=1,
         addr=["450 Kinnear Blvd, Suite 300", "Colorado Springs, CO 80920"], phone="(719) 555-0189",
         web="quarrystonetech.io", logo="bars", color=(0.20, 0.25, 0.55), font="helv",
         num=lambda s, d: f"QTP-{s}", seq0=4410, rate=(3, 5),
         date_fmt="abbr", money="dollar", terms=None, due_mode="date", due_days=15,
         labels=dict(num="Invoice", date="Issued", due="Due", sub="Subtotal", tax="Tax", total="Total"),
         tax=None, total_style="total", acct=None,
         fixed=[F("Managed IT - Essentials plan (6 seats)", 6, "seat", "38.00"),
                F("Microsoft 365 Business Standard", 6, "lic", "12.50"),
                F("Backup & M365 retention", 1, "mo", "45.00")],
         extra_items=[I("HRS", "Project hours - outside plan", "hr", 125, 125, 1, 4, tax=False, kind="labor", step=0.5)],
         extra_p=0.4, channel=(0.95, 0.03, 0.02), notes=["Questions about this invoice? billing@quarrystonetech.io"]),

    # --- field-service tickets
    dict(key="haskett", slug="haskett-plumbing", name="Haskett & Sons Plumbing",
         layout="service_ticket", n=3,
         addr=["1022 Pelton Ave", "Fenmore, CO 80947"], phone="(719) 555-0158",
         web=None, logo="drop", color=(0.10, 0.30, 0.60), font="helv",
         num=lambda s, d: f"W-{s}", seq0=18204, rate=(4, 6),
         date_fmt="mdy", money="dollar", terms="Upon receipt", due_mode="terms_in_due",
         labels=dict(num="Invoice No.", date="Date", due="Due Date"),
         tax=D("0.0765"), total_style="total", acct=None, rate_hr=D("118.00"),
         items=[I("LAB", "Labor - journeyman plumber", "HR", 118, 118, 1, 4, tax=False, kind="labor", step=0.5),
                I("TRP", "Trip charge", "EA", 65, 65, 1, 1, tax=False, kind="fee"),
                I("PRV-34", "3/4\" pressure reducing valve", "EA", 84.20, 84.20, 1, 1),
                I("FD-3", "3\" floor drain strainer, SS", "EA", 22.60, 22.60, 1, 2),
                I("PEX-34", "3/4\" PEX tubing, per ft", "FT", 1.15, 1.15, 10, 30, step=5),
                I("BV-34", "3/4\" ball valve, lead free", "EA", 18.40, 18.40, 1, 2),
                I("JET", "Drain line jetting", "EA", 245, 245, 1, 1, tax=False, kind="svc")],
         nitems=(3, 5), always=["LAB", "TRP"], channel=(0.2, 0.3, 0.5),
         work=["Floor drain in brewhouse backing up. Cleared and jetted 3\" line to main.",
               "Replaced failed pressure reducing valve at main shutoff. Tested at 62 psi.",
               "Leak at glycol room hose bib. Replaced valve and section of PEX."]),
    dict(key="ambervolt", slug="ambervolt-electric", name="Ambervolt Electric LLC",
         layout="service_ticket", n=3,
         addr=["515 Ruddock St", "Calder Springs, CO 80909"], phone="(719) 555-0126",
         web="ambervoltelectric.com", logo="bolt", color=(0.85, 0.55, 0.05), font="vera",
         num=lambda s, d: f"AE{s}", seq0=5530, rate=(2, 4),
         date_fmt="mdy_np", money="dollar", terms="Net 15", due_mode="none",
         labels=dict(num="Invoice", date="Date", terms="Terms"),
         tax=D("0.0765"), total_style="total", acct=None,
         items=[I("LAB", "Electrician labor", "HR", 135, 135, 1, 6, tax=False, kind="labor", step=0.5),
                I("APP", "Apprentice labor", "HR", 72, 72, 1, 6, tax=False, kind="labor", step=0.5),
                I("BRK30", "30A 2-pole breaker", "EA", 46.80, 46.80, 1, 2),
                I("WR10", "#10 THHN wire, per ft", "FT", .62, .62, 50, 200, step=25),
                I("REC-L630", "L6-30 receptacle", "EA", 28.90, 28.90, 1, 2),
                I("EMT34", "3/4\" EMT conduit, 10 ft", "EA", 11.75, 11.75, 2, 8),
                I("PRM", "Permit fee", "EA", 95, 95, 1, 1, tax=False, kind="fee")],
         nitems=(3, 6), always=["LAB"], channel=(0.5, 0.3, 0.2),
         work=["Install 30A 240V circuit for new keg washer.", "Troubleshoot tripping breaker on walk-in cooler circuit.",
               "Add two dedicated circuits in canning area per drawing."]),
    dict(key="steelhead", slug="steelhead-pump", name="Steelhead Pump & Valve Repair",
         layout="service_ticket", n=2,
         addr=["77 Mill Race Rd", "Pueblo, CO 81003"], phone="(719) 555-0193",
         web=None, logo="gear", color=(0.25, 0.30, 0.35), font="helv",
         num=lambda s, d: f"SPV-{s}", seq0=2207, rate=(1, 2),
         date_fmt="mdy", money="plain", terms="Net 30", due_mode="date",
         labels=dict(num="Invoice No.", date="Invoice Date", due="Due Date"),
         tax=D("0.0765"), total_style="total", acct=None,
         items=[I("LAB", "Shop labor", "HR", 98, 98, 2, 6, tax=False, kind="labor", step=0.5),
                I("SEAL", "Mechanical seal kit, 1.5 hp centrifugal", "EA", 138.00, 138.00, 1, 1),
                I("IMP", "Impeller, 316SS", "EA", 212.00, 212.00, 1, 1),
                I("BRG", "Motor bearings (pair)", "EA", 44.00, 44.00, 1, 1),
                I("PU", "Pickup & return", "EA", 75, 75, 1, 1, tax=False, kind="fee")],
         nitems=(3, 4), always=["LAB"], channel=(0.3, 0.4, 0.3),
         work=["Rebuilt 1.5 hp CIP pump: new seal and bearings, tested.", "Replaced cracked impeller on transfer pump."]),

    # --- sidebar
    dict(key="coldwater", slug="coldwater-refrigeration", name="Coldwater Ridge Refrigeration",
         layout="sidebar", n=4,
         addr=["2390 Barrow Creek Rd", "Monument, CO 80132"], phone="(719) 555-0140",
         web="coldwaterridge.com", logo="flake", color=(0.10, 0.45, 0.60), font="helv",
         num=lambda s, d: f"{d.year}-{s:04d}", seq0=310, rate=(1.5, 2.5), yearly=True,
         date_fmt="mdy", money="dollar", terms="Net 15", due_mode="date",
         tax=D("0.0765"), total_style="total", acct=None,
         items=[I("SVC", "Refrigeration technician", "HR", 128, 128, 1, 4, tax=False, kind="labor", step=0.5),
                I("R404", "R-404A refrigerant", "LB", 24.50, 24.50, 2, 10),
                I("TXV", "Thermostatic expansion valve", "EA", 186.00, 186.00, 1, 1),
                I("FAN", "Evaporator fan motor 9W", "EA", 92.40, 92.40, 1, 2),
                I("GLY", "Propylene glycol, 5 gal", "PL", 108.00, 108.00, 1, 2),
                I("PM", "Preventive maintenance - glycol chiller", "EA", 225.00, 225.00, 1, 1, tax=False, kind="svc")],
         nitems=(2, 4), channel=(0.6, 0.2, 0.2), notes=["Warranty: 90 days parts and labor on repairs."]),
    dict(key="thimbleworth", slug="thimbleworth-print", name="Thimbleworth Screen Printing",
         layout="sidebar", n=1,
         addr=["63 Cobbler St", "Manitou Springs, CO 80829"], phone="(719) 555-0114",
         web="thimbleworthprint.com", logo="circle", color=(0.75, 0.25, 0.30), font="vera",
         num=lambda s, d: f"T{s}", seq0=1180, rate=(1, 2),
         date_fmt="abbr", money="dollar", terms="Payment due in 30 days", due_mode="none",
         tax=D("0.0765"), total_style="total_balance", acct=None, deposit_p=1.0,
         items=[I("TEE", "Staff tee, 2-color front, S-XXL", "EA", 11.40, 11.40, 24, 72, step=12),
                I("HOOD", "Pullover hoodie, 1-color", "EA", 26.80, 26.80, 12, 24, step=6),
                I("SCR", "Screen setup", "EA", 25.00, 25.00, 2, 3)],
         nitems=(2, 3), always=["SCR"], channel=(0.6, 0.2, 0.2), notes=["Thank you for supporting local makers!"]),

    # --- counter tickets
    dict(key="mapleford", slug="mapleford-lumber", name="MAPLEFORD LUMBER & HARDWARE",
         layout="counter_receipt", n=2,
         addr=["310 Depot St", "Fenmore, CO 80947"], phone="(719) 555-0101",
         web=None, logo=None, color=(0.0, 0.0, 0.0), font="courier",
         num=lambda s, d: f"{s // 100000:02d}-{s % 100000:05d}", seq0=1288410, rate=(80, 120),
         date_fmt="mdy2", money="plain", terms="10th Prox", due_mode="none",
         labels=dict(num="INVOICE", date="DATE", terms="TERMS", sub="SUBTOTAL", tax="TAX", total="TOTAL",
                     po="PO", acct="ACCT"),
         tax=D("0.0765"), total_style="total", acct="OWLSE01",
         items=[I("2X4-8", "2X4X8 SPF STUD", "EA", 4.28, 4.28, 4, 20),
                I("OSB716", "OSB 7/16 4X8", "EA", 17.95, 17.95, 1, 4),
                I("SCR-3", "DECK SCREW #10X3 5LB", "EA", 38.99, 38.99, 1, 1),
                I("ANC-38", "WEDGE ANCHOR 3/8X3 25PK", "EA", 21.49, 21.49, 1, 2),
                I("PNT-GAL", "EXT PAINT SATIN GAL", "EA", 46.99, 46.99, 1, 2),
                I("CAULK", "SILICONE CAULK CLR", "EA", 8.49, 8.49, 1, 4)],
         nitems=(2, 5), page=(306.0, 648.0), channel=(0.2, 0.2, 0.6),
         notes=["CHARGE TO ACCOUNT", "NO RETURNS ON SPECIAL ORDERS"]),
    dict(key="ridgeback", slug="ridgeback-parts", name="Ridgeback Auto & Fleet Parts",
         layout="counter_receipt", n=2,
         addr=["1740 S. Tejon Frontage Rd", "Calder Springs, CO 80906"], phone="(719) 555-0167",
         web=None, logo=None, color=(0.0, 0.0, 0.0), font="helv",
         num=lambda s, d: f"R{s}", seq0=604122, rate=(40, 60),
         date_fmt="mdy", money="plain", terms="Net 30", due_mode="date",
         labels=dict(num="INV#", date="DATE", due="DUE", terms="TERMS", sub="SUBTOTAL", tax="TAX", total="TOTAL",
                     po="PO#", acct="ACCT#"),
         tax=D("0.0765"), total_style="total", acct="40917",
         items=[I("WB-22", "WIPER BLADE 22IN", "EA", 14.99, 14.99, 2, 2),
                I("OF-51", "OIL FILTER PH3600 EQ", "EA", 6.89, 6.89, 1, 2),
                I("OIL-5W30", "5W-30 SYN BLEND 5QT", "EA", 27.49, 27.49, 1, 2),
                I("BAT-65", "BATTERY GRP 65 850CCA", "EA", 168.99, 168.99, 1, 1),
                I("CORE", "BATTERY CORE CHARGE", "EA", 22.00, 22.00, 1, 1),
                I("DEF", "DEF FLUID 2.5 GAL", "EA", 12.99, 12.99, 1, 2)],
         nitems=(2, 4), page=(306.0, 612.0), channel=(0.2, 0.2, 0.6),
         notes=["CORE RETURN WITHIN 30 DAYS FOR CREDIT"]),

    # --- classic letterhead
    dict(key="brightfield", slug="brightfield-pest", name="Brightfield Pest Solutions",
         layout="classic", n=6, cadence="monthly", bill_day=20,
         addr=["845 Larkin Rd", "Calder Springs, CO 80915"], phone="(719) 555-0135",
         web="brightfieldpest.com", logo="leaf", color=(0.35, 0.55, 0.15), font="helv",
         num=lambda s, d: f"BPS{s}", seq0=77310, rate=(18, 26),
         date_fmt="mdy", money="dollar", terms="Net 30", due_mode="none",
         tax=D("0.0765"), total_style="total", acct="BP-3318",
         fixed=[F("Monthly commercial pest service - food facility", 1, "EA", "89.00", tax=True),
                F("Rodent bait station service (8 stations)", 8, "EA", "4.50", tax=True)],
         extra_items=[I("FLY", "Fly light bulb replacement", "EA", 18.50, 18.50, 2, 4)], extra_p=0.25,
         channel=(0.5, 0.3, 0.2), notes=["Service report left on site. Please keep doors closed after dusk."]),
    dict(key="pinyon", slug="pinyon-lab", name="Pinyon Analytical Laboratory",
         layout="classic", n=2, no_sub=True,
         addr=["2015 Prospect Ridge Dr", "Fort Collins, CO 80525"], phone="(970) 555-0108",
         web="pinyonlab.com", logo="hex", color=(0.30, 0.20, 0.50), font="times",
         num=lambda s, d: f"PAL-{d.year % 100:02d}-{s:05d}", seq0=1230, rate=(6, 9),
         date_fmt="long", money="dollar", terms="Net 30", due_mode="date",
         labels=dict(num="Invoice No.", date="Invoice Date", tax="Tax", total="Total Due"),
         tax=None, total_style="total", acct="C-0947",
         items=[I("ABV", "Alcohol/extract panel (ABV, OG, RE)", "SMP", 32.00, 32.00, 2, 6, tax=False, kind="svc"),
                I("MICRO", "Microbial panel - PCR spoilage", "SMP", 75.00, 75.00, 1, 3, tax=False, kind="svc"),
                I("IBU", "Bitterness (IBU)", "SMP", 28.00, 28.00, 1, 4, tax=False, kind="svc"),
                I("DO", "Dissolved oxygen, packaged", "SMP", 22.00, 22.00, 2, 6, tax=False, kind="svc")],
         nitems=(2, 3), channel=(0.8, 0.1, 0.1), notes=["Results reported via client portal."]),
    dict(key="basalt", slug="basalt-rental", name="Basalt Equipment Rental",
         layout="classic", n=2,
         addr=["9100 Industrial Blvd", "Calder Springs, CO 80916"], phone="(719) 555-0124",
         web="basaltrental.com", logo="mountain", color=(0.30, 0.30, 0.30), font="vera",
         num=lambda s, d: f"BER-{s}", seq0=661020, rate=(30, 45),
         date_fmt="mdy", money="dollar", terms="Due upon receipt", due_mode="none",
         tax=D("0.0765"), total_style="total", acct="R-55120", rental=True,
         items=[I("SCS19", "Scissor lift 19' electric - weekly", "WK", 245.00, 245.00, 1, 1),
                I("FLT5K", "Forklift 5,000 lb LP - daily", "DAY", 189.00, 189.00, 1, 3),
                I("DMG", "Damage waiver 12%", "EA", 29.40, 29.40, 1, 1),
                I("DEL", "Delivery & pickup", "EA", 120.00, 120.00, 1, 1, tax=False, kind="fee"),
                I("ENV", "Environmental fee", "EA", 9.50, 9.50, 1, 1)],
         nitems=(2, 4), always=["DEL"], channel=(0.5, 0.3, 0.2),
         notes=["Rental charges are based on time out, not usage."]),

    # --- centered letterhead
    dict(key="inkhorn", slug="inkhorn-press", name="Inkhorn Press & Label",
         layout="centered", n=4,
         addr=["127 Ledger St", "Calder Springs, CO 80903"], phone="(719) 555-0153",
         web="inkhornpress.com", logo=None, color=(0.15, 0.15, 0.35), font="times",
         num=lambda s, d: f"{s:06d}", seq0=8812, rate=(3, 5),
         date_fmt="long", money="dollar", terms="Net 30", due_mode="date",
         labels=dict(num="Invoice No.", date="Date", due="Payment Due"),
         tax=D("0.0765"), total_style="total_balance", acct=None, deposit_p=0.3,
         items=[I("LBL-16", "16 oz can labels, 4-color, gloss", "M", 148.00, 148.00, 2, 10),
                I("TAP", "Tap handle decals", "EA", 2.10, 2.10, 25, 100, step=25),
                I("MENU", "Taproom menu cards, 11x17", "EA", 1.85, 1.85, 50, 150, step=50),
                I("PLT", "Plate change / art setup", "EA", 60.00, 60.00, 1, 2)],
         nitems=(2, 3), channel=(0.5, 0.3, 0.2), notes=["We appreciate your business."]),
    dict(key="brassick", slug="brassick-fab", name="Brassick Metal Fabrication",
         layout="centered", n=1, no_terms=True,
         addr=["40 Anvil Way", "Florence, CO 81226"], phone="(719) 555-0198",
         web=None, logo=None, color=(0.20, 0.20, 0.20), font="helv",
         num=lambda s, d: f"BMF{s}", seq0=402, rate=(0.3, 0.6),
         date_fmt="mdy", money="dollar", terms=None, due_mode="none",
         tax=D("0.0765"), total_style="total", acct=None,
         items=[I("RAIL", "Steel bar rail with footrest, 12 ft, powder coat", "EA", 1240.00, 1240.00, 1, 1),
                I("BRKT", "Wall brackets, fabricated", "EA", 38.00, 38.00, 4, 8),
                I("INST", "Installation labor", "HR", 85.00, 85.00, 4, 8, tax=False, kind="labor")],
         nitems=(2, 3), channel=(0.3, 0.3, 0.4), notes=["Thank you."]),

    # --- boxed carbon forms
    dict(key="tinplate", slug="tinplate-signs", name="Tinplate Sign Works",
         layout="boxed_form", n=2,
         addr=["212 N. Main St", "Fenmore, CO 80947"], phone="(719) 555-0176",
         web=None, logo="diamond", color=(0.70, 0.10, 0.12), font="helv",
         num=lambda s, d: f"{s:06d}", seq0=3381, rate=(0.8, 1.4),
         date_fmt="mdy_np", money="plain", terms="Due on receipt", due_mode="none",
         tax=D("0.0765"), total_style="total_balance", acct=None, deposit_p=0.5,
         items=[I("SGN", "Hand-painted wall sign 4x8", "EA", 1650.00, 1650.00, 1, 1),
                I("VNL", "Window vinyl lettering", "EA", 180.00, 180.00, 1, 2),
                I("AFR", "A-frame sidewalk sign", "EA", 225.00, 225.00, 1, 1),
                I("INS", "Install", "EA", 150.00, 150.00, 1, 1, tax=False, kind="labor")],
         nitems=(2, 3), channel=(0.1, 0.4, 0.5)),
    dict(key="gullwing", slug="gullwing-grounds", name="Gullwing Grounds & Snow",
         layout="boxed_form", n=3,
         addr=["PO Box 228", "Fenmore, CO 80947"], phone="(719) 555-0145",
         web=None, logo="flake", color=(0.10, 0.35, 0.20), font="helv",
         num=lambda s, d: f"{s:05d}", seq0=1904, rate=(0.6, 1.0),
         date_fmt="mdy_np", money="plain", terms="Net 15", due_mode="terms_in_due",
         tax=None, total_style="total", acct=None,
         items=[I("PLW", "Snow plow - lot", "EA", 145.00, 145.00, 1, 4, tax=False, kind="svc"),
                I("ICE", "Ice melt application", "EA", 65.00, 65.00, 1, 4, tax=False, kind="svc"),
                I("WLK", "Shovel walks & entry", "EA", 40.00, 40.00, 1, 4, tax=False, kind="svc"),
                I("MOW", "Mow & trim", "EA", 75.00, 75.00, 1, 4, tax=False, kind="svc")],
         nitems=(2, 3), channel=(0.1, 0.4, 0.5)),

    # --- professional services letters
    dict(key="marrowby", slug="marrowby-quist", name="Marrowby & Quist, CPAs",
         layout="professional", n=2, no_sub=True, no_tax=True,
         addr=["600 Harrow Plaza, Suite 1150", "Colorado Springs, CO 80903"], phone="(719) 555-0110",
         web="marrowbyquist.com", logo=None, color=(0.10, 0.20, 0.30), font="times",
         num=lambda s, d: f"{s}", seq0=24410, rate=(4, 6),
         date_fmt="long", money="dollar", terms="Due upon receipt", due_mode="none",
         labels=dict(num="Invoice No.", date="Date", total="Amount Due"),
         tax=None, total_style="due", acct="OWL100", hourly=D("165.00"),
         tasks=["Monthly bookkeeping review and reconciliation", "Sales tax return preparation",
                "Payroll tax filings (941, state)", "Year-end adjusting entries", "TTB excise tax return review",
                "Telephone consultation re: equipment depreciation"],
         channel=(0.8, 0.15, 0.05)),
    dict(key="odell", slug="odell-brannock", name="Odell Brannock, Attorney at Law",
         layout="professional", n=1, no_sub=True, no_tax=True,
         addr=["22 Courthouse Sq", "Fenmore, CO 80947"], phone="(719) 555-0182",
         web=None, logo=None, color=(0.25, 0.10, 0.10), font="times",
         num=lambda s, d: f"{d.year}-{s:03d}", seq0=140, rate=(0.4, 0.7), yearly=True,
         date_fmt="long", money="plain", terms="Net 30", due_mode="none",
         labels=dict(num="Invoice", date="Date", terms="Terms", total="Total Amount Due"),
         tax=None, total_style="due", acct=None, hourly=D("240.00"),
         tasks=["Review taproom lease amendment", "Correspondence with landlord counsel",
                "Draft liquor license transfer documents", "Telephone conference with client"],
         channel=(0.6, 0.3, 0.1)),
    dict(key="juno", slug="juno-whitlock", name="Juno Whitlock Design Studio",
         layout="professional", n=2,
         addr=["818 Pine Hollow Dr", "Fenmore, CO 80947"], phone="(719) 555-0133",
         web="junowhitlock.design", logo=None, color=(0.55, 0.20, 0.35), font="vera",
         num=lambda s, d: f"JW-{s:03d}", seq0=88, rate=(0.2, 0.4),
         date_fmt="abbr", money="dollar", terms=None, due_mode="date", due_days=14,
         labels=dict(num="Invoice", date="Date", due="Payment due", tax="Sales tax", total="Total due"),
         tax=None, total_style="total", acct=None, hourly=D("85.00"),
         tasks=["Seasonal can label design - concept round", "Label revisions and print-ready files",
                "Taproom chalkboard menu layout", "Social media graphics pack"],
         channel=(0.95, 0.03, 0.02)),

    # --- plain word-processor templates
    dict(key="pruett", slug="pruett-handyman", name="Dale Pruett Handyman Services",
         layout="plain_template", n=2, no_number=True, no_tax=True,
         addr=["4417 Wicklow Ct", "Fenmore, CO 80947"], phone="(719) 555-0161",
         web=None, logo=None, color=(0.0, 0.0, 0.0), font="times",
         num=lambda s, d: "", seq0=0, rate=(0, 0),
         date_fmt="mdy_np", money="dollar", terms="Due on receipt", due_mode="none",
         tax=None, total_style="total", acct=None,
         items=[I("", "Labor", "hr", 55.00, 55.00, 3, 10, tax=False, kind="labor", step=0.5),
                I("", "Materials (receipts attached)", "lot", 18.00, 140.00, 1, 1, tax=False),
                I("", "Haul-away", "ea", 40.00, 40.00, 1, 1, tax=False, kind="fee")],
         nitems=(2, 3), always=[0], channel=(0.2, 0.2, 0.6),
         notes=["Make checks payable to Dale Pruett. Thank you!"]),
    dict(key="wren", slug="wren-hollow-windows", name="Wren Hollow Window Cleaning",
         layout="plain_template", n=2,
         addr=["1309 Sorrel Ln", "Fenmore, CO 80947"], phone="(719) 555-0179",
         web=None, logo=None, color=(0.15, 0.40, 0.55), font="helv",
         num=lambda s, d: f"{s}", seq0=1041, rate=(0.5, 0.8),
         date_fmt="mdy", money="dollar", terms="Due upon receipt", due_mode="none",
         labels=dict(num="Invoice #", date="Date", sub="Subtotal", tax="Tax", total="Total"),
         tax=None, total_style="total", acct=None,
         items=[I("", "Storefront windows - interior & exterior", "ea", 145.00, 145.00, 1, 1, tax=False, kind="svc"),
                I("", "Taproom glass partition", "ea", 60.00, 60.00, 1, 1, tax=False, kind="svc"),
                I("", "Skylights", "ea", 85.00, 85.00, 1, 1, tax=False, kind="svc")],
         nitems=(2, 3), always=[0], channel=(0.6, 0.2, 0.2), notes=["Thanks for choosing Wren Hollow!"]),
]

LAYOUTS = ["classic", "banded", "centered", "sidebar", "service_ticket", "dotmatrix", "minimal",
           "counter_receipt", "quickbooks", "boxed_form", "professional", "plain_template", "utility_stub"]


def finish_supplier(s):
    lab = dict(BASE_LABELS)
    lab.update(s.get("labels", {}))
    s["labels"] = lab
    s.setdefault("items", [])
    s.setdefault("fixed", [])
    s.setdefault("extra_items", [])
    s.setdefault("nitems", (1, 1))
    s.setdefault("always", [])
    s.setdefault("notes", [])
    s.setdefault("page", LETTER)
    s.setdefault("legal", s["name"])
    s.setdefault("email", None)
    if s.get("web") and not s.get("email"):
        s["email"] = "billing@" + s["web"]
    s["fonts"] = FONTS[s["font"]]
    s["item_money"] = "plain" if s["money"] in ("dollar", "dollar_sp") else s["money"]
    return s


# ----------------------------------------------------------------------------- the rules (labels)
TERM_NET = re.compile(r"^net (\d+)$")
TERM_DISC = re.compile(r"^\d+(?:\.\d+)?%?/\d+ net (\d+)$")
TERM_RECEIPT = re.compile(r"^(due )?(on|upon) receipt$")


def due_from_terms(terms, inv_date):
    """Apply the brief's due-date rules to printed terms. None when the rule leaves it null."""
    t = " ".join(terms.lower().split())
    if TERM_RECEIPT.match(t):
        return inv_date
    m = TERM_NET.match(t) or TERM_DISC.match(t)
    if m:
        return inv_date + timedelta(days=int(m.group(1)))
    return None


def derive_label(inv):
    sup = inv["sup"]
    number = inv["number"] if inv["number_mode"] == "printed" else None
    inv_date = inv["date"] if inv["date_mode"] == "printed" else None
    terms = inv["terms_printed"]
    due, src = None, None
    if inv["due_printed"] is not None:
        due, src = inv["due_printed"], "printed"
    elif terms and inv_date:
        due = due_from_terms(terms, inv_date)
        src = "terms" if due else None
    rows = inv["total_rows"]
    sub = next((r["amt"] for r in rows if r["kind"] == "sub" and r["amt"] is not None), None)
    tax = next((r["amt"] for r in rows if r["kind"] == "tax" and r["amt"] is not None), None)
    total = next((r["amt"] for r in rows if r["kind"] == "total" and r["amt"] is not None), None)
    if total is None and not any(r["kind"] == "total" and r["amt"] is not None for r in rows):
        total = next((r["amt"] for r in rows if r["kind"] == "due" and r["amt"] is not None), None)
    lab = dict(supplier=sup["name"], invoice_number=number,
               invoice_date=inv_date.isoformat() if inv_date else None,
               due_date=due.isoformat() if due else None,
               subtotal=s2(sub), tax=s2(tax), total=s2(total))
    reasons = [f"missing_{f}" for f in FIELDS if lab[f] is None]
    if due and inv_date and due < inv_date:
        reasons.append("due_before_invoice")
    if sub is not None and tax is not None and total is not None and abs(q2(sub) + q2(tax) - q2(total)) > D("0.01"):
        reasons.append("total_mismatch")
    lab.update(exception=bool(reasons), exception_reasons=reasons, terms=terms, due_source=src)
    return lab


# ----------------------------------------------------------------------------- schedule and values
def month_list():
    out, d = [], date(WINDOW[0].year, WINDOW[0].month, 1)
    while d <= WINDOW[1]:
        out.append(d)
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


def schedule_dates(sup, rng):
    n, cad = sup["n"], sup.get("cadence")
    lo, hi = WINDOW
    if cad == "monthly":
        months = sorted(rng.sample(month_list()[:-1], n))
        return [date(m.year, m.month, min(sup["bill_day"] + rng.randint(0, 2), 28)) for m in months]
    if cad == "quarterly":
        qs = [m for m in month_list()[:-1] if m.month in (1, 4, 7, 10)]
        return [date(m.year, m.month, sup["bill_day"]) for m in sorted(rng.sample(qs, n))]
    out = set()
    while len(out) < n:
        d = lo + timedelta(days=rng.randint(0, (hi - lo).days))
        if cad == "weekly":
            d -= timedelta(days=(d.weekday() - 1) % 7)  # route day: Tuesday
            if d < lo:
                continue
        elif d.weekday() == 6 and rng.random() < 0.85:
            continue
        out.add(d)
    return sorted(out)


def price_for(sup, spec, d, rng):
    base = D(str(spec["plo"])) if spec["plo"] == spec["phi"] else q2(D(str(rng.uniform(spec["plo"], spec["phi"]))))
    years = max(0, d.year - 2025 + (1 if d.month >= 3 else 0)) if d.year >= 2025 else 0
    if spec.get("kind") not in ("fee",) and years and sup["key"] not in ("pruett", "wren", "gullwing", "tinplate"):
        base = q2(base * D("1.03") ** years)
    return base


def make_lines(sup, d, rng):
    lines = []

    def add(desc, qty, unit, price, tax, sku="", kind="part"):
        qty = D(str(qty))
        lines.append(dict(sku=sku, desc=desc, unit=unit, qty=qty, price=q2(price), tax=tax, kind=kind,
                          amount=q2(qty * q2(price))))

    for f in sup["fixed"]:
        p = D(f["price"])
        if d >= date(2026, 1, 1):
            p = q2(p * D("1.04"))
        add(f["desc"], f["qty"], f["unit"], p, f["tax"], f["sku"], f["kind"])
    for spec in sup["extra_items"]:
        if rng.random() < sup.get("extra_p", 0):
            add(spec["desc"], qty_for(spec, rng), spec["unit"], price_for(sup, spec, d, rng), spec["tax"],
                spec["sku"], spec["kind"])
    if sup["items"]:
        k = rng.randint(*sup["nitems"])
        must = [s for i, s in enumerate(sup["items"]) if s["sku"] in sup["always"] or i in sup["always"]]
        rest = [s for s in sup["items"] if s not in must]
        chosen = must + rng.sample(rest, max(0, min(len(rest), k - len(must))))
        chosen.sort(key=lambda s: sup["items"].index(s))
        for spec in chosen:
            add(spec["desc"], qty_for(spec, rng), spec["unit"], price_for(sup, spec, d, rng), spec["tax"],
                spec["sku"], spec["kind"])
    if sup.get("surcharge"):
        desc, pct = sup["surcharge"]
        base = sum(l["amount"] for l in lines if l["kind"] in ("svc",))
        add(desc, 1, "EA", q2(base * pct), False, "FSC", "fee")
    if sup.get("hourly"):
        start = d - timedelta(days=rng.randint(20, 34))
        for t in rng.sample(sup["tasks"], rng.randint(2, min(4, len(sup["tasks"])))):
            day = start + timedelta(days=rng.randint(0, 20))
            hrs = D(rng.randint(1, 16)) * D("0.25")
            if sup["key"] == "juno":
                hrs = D(rng.randint(4, 24)) * D("0.5")
            add(t, hrs, "hr", sup["hourly"], False, "", "labor")
            lines[-1]["date"] = day
        lines.sort(key=lambda l: l.get("date", d))
        if sup["key"] == "odell":
            add("Recording and filing fees", 1, "", D("38.00"), False, "", "expense")
    return lines


def qty_for(spec, rng):
    step = D(str(spec["step"]))
    lo, hi = D(str(spec["qlo"])), D(str(spec["qhi"]))
    n = int((hi - lo) / step)
    return lo + step * rng.randint(0, max(0, n))


def build_invoices(seed):
    rng = random.Random(seed)
    sups = [finish_supplier(dict(s)) for s in SUPPLIERS]
    assert sum(s["n"] for s in sups) == 100, sum(s["n"] for s in sups)
    invs = []
    for sup in sups:
        srng = random.Random(f"{seed}-{sup['key']}")
        dates = schedule_dates(sup, srng)
        seq = sup["seq0"]
        prev = WINDOW[0]
        year = None
        for d in dates:
            if sup.get("yearly") and d.year != year:
                year, seq = d.year, sup["seq0"] + srng.randint(0, 20)
            seq += max(1, int((d - prev).days / 30.4 * srng.uniform(*sup["rate"]))) if sup["rate"][1] else 0
            prev = d
            invs.append(new_invoice(sup, d, seq, random.Random(f"{seed}-{sup['key']}-{d.isoformat()}")))
    invs.sort(key=lambda v: (v["date"], v["sup"]["key"]))
    for i, inv in enumerate(invs):
        inv["idx"] = i + 1
        inv["id"] = f"{i + 1:03d}-{inv['sup']['slug']}"
    inject_exceptions(invs, rng)
    for inv in invs:
        inv["total_rows"] = build_total_rows(inv)
        inv["label"] = derive_label(inv)
    assign_renders(invs, rng)
    return invs


def terms_days(t):
    if not t:
        return None
    d = due_from_terms(t, date(2000, 1, 1))
    return None if d is None else (d - date(2000, 1, 1)).days


def new_invoice(sup, d, seq, rng):
    lines = make_lines(sup, d, rng)
    sub = sum((l["amount"] for l in lines), D("0"))
    taxable = sum((l["amount"] for l in lines if l["tax"]), D("0"))
    tax = q2(taxable * sup["tax"]) if sup["tax"] else D("0.00")
    inv = dict(sup=sup, date=d, number=sup["num"](seq, d), lines=lines, rng_seed=rng.random(),
               number_mode="omit" if sup.get("no_number") else "printed", date_mode="printed",
               sub_mode="omit" if sup.get("no_sub") else "printed",
               tax_mode="omit" if sup.get("no_tax") else "printed", total_mode="printed",
               sub=sub, tax=tax, ship=D("0"), deposit=D("0"), injected=[])
    if sup.get("ship_after_sub"):
        inv["ship"] = q2(D(str(rng.choice([24.50, 31.80, 46.25, 58.90]))))
    inv["total"] = sub + tax + inv["ship"]
    if rng.random() < sup.get("deposit_p", 0):
        inv["deposit"] = q2((inv["total"] / 2).quantize(D("1"), ROUND_HALF_UP))
    # terms / due
    t = sup["terms"]
    inv["terms_printed"] = t if not sup.get("no_terms") else None
    inv["due_printed"] = None
    inv["due_slot_text"] = None
    if sup["due_mode"] == "date":
        n = terms_days(t) if t else sup["due_days"]
        inv["due_printed"] = d + timedelta(days=n)
    elif sup["due_mode"] == "terms_in_due":
        inv["due_slot_text"] = t
    # ordinary clutter
    inv["po"] = (f"{rng.randint(1000, 1999)}" if rng.random() < 0.55 else None) if sup["layout"] != "utility_stub" else None
    inv["site"] = CUSTOMER["sites"][1 if rng.random() < 0.5 else 0]
    inv["ship_date"] = d - timedelta(days=rng.randint(0, 3))
    inv["order_date"] = d - timedelta(days=rng.randint(3, 12))
    inv["rep"] = rng.choice(["JM", "KT", "RB", "AL", "DS", "House"])
    inv["via"] = rng.choice(["Our truck", "Best Way", "Customer pickup", "UPS Ground", "LTL"])
    inv["wo"] = f"{rng.randint(10000, 99999)}"
    inv["tech"] = rng.choice(["M. Ortega", "B. Lindqvist", "T. Haskett", "J. Rourke", "C. Nakamura", "R. Pell"])
    inv["bol"] = f"{rng.randint(100000, 999999)}"
    inv["time"] = f"{rng.randint(7, 17):02d}:{rng.randint(0, 59):02d}"
    inv["clerk"] = rng.choice(["DW", "SAM", "TOMR", "LIZ", "KEV"])
    inv["prev_bal"] = q2(inv["total"] * D(str(rng.uniform(0.85, 1.15))))
    inv["svc_from"] = date(d.year, d.month, 1)
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    inv["svc_to"] = nxt - timedelta(days=1)
    if sup.get("cadence") == "quarterly":
        inv["svc_to"] = date(nxt.year + (nxt.month > 10), (nxt.month + 1) % 12 + 1, 1) - timedelta(days=1)
    inv["work"] = rng.choice(sup["work"]) if sup.get("work") else None
    return inv


def inject_exceptions(invs, rng):
    """Planned page-level errors on otherwise ordinary invoices. Each is something real invoices do."""
    used_sup = {}

    def clean(inv):
        return not inv["injected"] and not intrinsic_exception(inv)

    def pick(pred, tag, n):
        for _ in range(n):
            cands = [v for v in invs if clean(v) and pred(v)]
            assert cands, tag
            cands.sort(key=lambda v: (used_sup.get(v["sup"]["key"], 0), v["idx"]))
            low = used_sup.get(cands[0]["sup"]["key"], 0)
            pool = [v for v in cands if used_sup.get(v["sup"]["key"], 0) == low]
            v = rng.choice(pool)
            used_sup[v["sup"]["key"]] = low + 1
            v["injected"].append(tag)
            yield v

    plain_totals = lambda v: v["sup"]["layout"] not in ("utility_stub",) and v["sub_mode"] == "printed" \
        and v["tax_mode"] == "printed" and v["sup"]["total_style"] in ("total", "due") and not v["ship"]
    # total_mismatch: total keyed without the tax
    for v in pick(lambda v: plain_totals(v) and v["tax"] > 0, "mm_tax_left_out", 2):
        v["total"] = v["sub"]
    # total_mismatch: transposed digits in the total
    for v in pick(lambda v: plain_totals(v) and v["total"] >= 100, "mm_transposed", 1):
        v["total"] = transpose(v["total"])
    # total_mismatch: subtotal not updated after the last line was added
    for v in pick(lambda v: plain_totals(v) and len(v["lines"]) >= 3, "mm_stale_subtotal", 1):
        v["sub_shown"] = v["sub"] - v["lines"][-1]["amount"]
    # due_before_invoice: due year typed as last year
    for v in pick(lambda v: v["sup"]["due_mode"] == "date" and v["date"].month <= 2
                  and v["sup"]["layout"] != "utility_stub", "due_year_typo", 2):
        v["due_printed"] = safe_replace(v["due_printed"], year=v["due_printed"].year - 1)
    # due_before_invoice: month digit wrong
    for v in pick(lambda v: v["sup"]["due_mode"] == "date" and v["due_printed"].month > 2
                  and v["sup"]["layout"] != "utility_stub", "due_month_typo", 1):
        v["due_printed"] = safe_replace(v["due_printed"], month=v["due_printed"].month - 2)
    # due_before_invoice: due date filled from the order date
    for v in pick(lambda v: v["sup"]["due_mode"] == "date", "due_from_order_date", 1):
        v["due_printed"] = v["order_date"]
    # missing invoice date: left blank on the form
    for v in pick(lambda v: v["sup"]["layout"] in ("boxed_form", "service_ticket", "plain_template")
                  and v["sup"]["due_mode"] == "date", "date_blank_due_printed", 1):
        v["date_mode"] = "blank"
    for v in pick(lambda v: v["sup"]["layout"] in ("boxed_form", "service_ticket", "plain_template")
                  and v["sup"]["due_mode"] != "date" and v["terms_printed"], "date_blank", 1):
        v["date_mode"] = "blank"
    # missing invoice number: left blank
    for v in pick(lambda v: v["sup"]["layout"] in ("service_ticket", "plain_template", "classic")
                  and v["number_mode"] == "printed", "number_blank", 1):
        v["number_mode"] = "blank"
    # missing total: total box never filled in
    for v in pick(lambda v: v["sup"]["layout"] == "boxed_form" and v["sup"]["total_style"] == "total",
                  "total_blank", 1):
        v["total_mode"] = "blank"
    # not exceptions: printed due date disagrees with printed terms (printed date wins)
    for v in pick(lambda v: v["sup"]["due_mode"] == "date" and v["terms_printed"]
                  and terms_days(v["terms_printed"]) and terms_days(v["terms_printed"]) >= 15, "due_terms_conflict", 2):
        v["due_printed"] = v["date"] + timedelta(days=terms_days(v["terms_printed"]) + rng.choice([-10, 15]))


def intrinsic_exception(inv):
    s = inv["sup"]
    return bool(s.get("no_number") or s.get("no_sub") or s.get("no_tax") or s.get("no_terms")
                or s.get("ship_after_sub") or (s["terms"] and s["due_mode"] != "date"
                                               and terms_days(s["terms"]) is None))


def safe_replace(d, **kw):
    try:
        return d.replace(**kw)
    except ValueError:
        return (d - timedelta(days=3)).replace(**kw)


def transpose(v):
    s = f"{q2(v):.2f}"
    ip, fp = s.split(".")
    for i in range(len(ip) - 2, -1, -1):
        if ip[i] != ip[i + 1]:
            ip = ip[:i] + ip[i + 1] + ip[i] + ip[i + 2:]
            return D(ip + "." + fp)
    return v + D("9.00")


def build_total_rows(inv):
    sup, L = inv["sup"], inv["sup"]["labels"]
    rows = []
    if inv["sub_mode"] == "printed":
        rows.append(dict(label=L["sub"], amt=inv.get("sub_shown", inv["sub"]), kind="sub"))
    if inv["ship"]:
        rows.append(dict(label="Shipping & Handling", amt=inv["ship"], kind="other"))
    if inv["tax_mode"] == "printed":
        tl = L["tax"]
        if sup["tax"] and sup["layout"] in ("quickbooks", "classic", "banded", "sidebar", "centered"):
            tl = f"{tl} ({sup['tax'] * 100:.2f}%)"
        rows.append(dict(label=tl, amt=inv["tax"], kind="tax"))
    ts = sup["total_style"]
    if inv["total_mode"] == "blank":
        rows.append(dict(label=L["total"], amt=None, kind="total"))
    elif ts == "total":
        rows.append(dict(label=L["total"], amt=inv["total"], kind="total"))
    elif ts == "due":
        rows.append(dict(label=L["total"], amt=inv["total"], kind="due"))
    elif ts == "total_balance":
        rows.append(dict(label=L["total"], amt=inv["total"], kind="total"))
        rows.append(dict(label="Deposit received" if inv["deposit"] else L["paid"], amt=-inv["deposit"], kind="other"))
        rows.append(dict(label=L["balance"], amt=inv["total"] - inv["deposit"], kind="due"))
    return rows


def assign_renders(invs, rng):
    """Exact 50/25/25 split, stratified by exception status, nudged by each supplier's mail habits."""
    for group in (True, False):
        g = [v for v in invs if v["label"]["exception"] == group]
        n = len(g)
        target = {"scan": round(n / 4), "photo": round(n / 4)}
        target["clean"] = n - target["scan"] - target["photo"]
        if not group:
            ex = [v for v in invs if v["label"]["exception"]]
            ex_t = {"scan": round(len(ex) / 4), "photo": round(len(ex) / 4)}
            ex_t["clean"] = len(ex) - ex_t["scan"] - ex_t["photo"]
            target = {"clean": 50 - ex_t["clean"], "scan": 25 - ex_t["scan"], "photo": 25 - ex_t["photo"]}
        scored = []
        for v in g:
            w = v["sup"]["channel"]
            for r, wt in zip(("clean", "scan", "photo"), w):
                scored.append((-(wt * rng.uniform(0.4, 1.6)), v["idx"], r, v))
        scored.sort(key=lambda t: (t[0], t[1]))
        left = dict(target)
        for _, _, r, v in scored:
            if "render" in v or left[r] == 0:
                continue
            v["render"] = r
            left[r] -= 1
        for v in g:
            if "render" not in v:
                r = next(k for k in ("clean", "scan", "photo") if left[k] > 0)
                v["render"] = r
                left[r] -= 1
        assert all(x == 0 for x in left.values()), left


# ----------------------------------------------------------------------------- drawing helpers
class Pg:
    def __init__(self, c, inv):
        self.c, self.inv, self.sup = c, inv, inv["sup"]
        self.W, self.H = inv["sup"]["page"]
        self.f = self.sup["fonts"]
        self.col = self.sup["color"]
        self.ink = (0.08, 0.08, 0.10)
        self.drawn = []
        self.L = self.sup["labels"]

    def font(self, w):
        return self.f.get(w, w)

    def t(self, x, y, s, size=9, w="r", align="l", color=None, cs=0):
        if s is None or s == "":
            return
        s = str(s)
        c = self.c
        c.setFillColorRGB(*(color or self.ink))
        fn = self.font(w)
        if cs:
            tw = pdfmetrics.stringWidth(s, fn, size) + cs * (len(s) - 1)
            x0 = x - tw if align == "r" else x - tw / 2 if align == "c" else x
            to = c.beginText(x0, y)
            to.setFont(fn, size)
            to.setCharSpace(cs)
            to.textOut(s)
            to.setCharSpace(0)
            c.drawText(to)
        else:
            c.setFont(fn, size)
            {"l": c.drawString, "r": c.drawRightString, "c": c.drawCentredString}[align](x, y, s)
        self.drawn.append(s)

    def sw(self, s, size=9, w="r"):
        return pdfmetrics.stringWidth(str(s), self.font(w), size)

    def wrap(self, s, width, size=9, w="r"):
        words, lines, cur = str(s).split(), [], ""
        for wd in words:
            nxt = (cur + " " + wd).strip()
            if self.sw(nxt, size, w) <= width or not cur:
                cur = nxt
            else:
                lines.append(cur)
                cur = wd
        if cur:
            lines.append(cur)
        return lines

    def line(self, x1, y1, x2, y2, lw=0.5, color=None, dash=None):
        c = self.c
        c.setStrokeColorRGB(*(color or self.ink))
        c.setLineWidth(lw)
        c.setDash(*(dash or ([], 0)))
        c.line(x1, y1, x2, y2)
        c.setDash([], 0)

    def rect(self, x, y, w, h, fill=None, stroke=None, lw=0.5, r=0):
        c = self.c
        if fill:
            c.setFillColorRGB(*fill)
        if stroke:
            c.setStrokeColorRGB(*stroke)
        c.setLineWidth(lw)
        if r:
            c.roundRect(x, y, w, h, r, stroke=1 if stroke else 0, fill=1 if fill else 0)
        else:
            c.rect(x, y, w, h, stroke=1 if stroke else 0, fill=1 if fill else 0)

    def m(self, v):
        return fmt_money(v, self.sup["money"], self.sup.get("neg", "minus"))

    def im(self, v):
        return fmt_money(v, self.sup["item_money"])

    def d(self, dt):
        return fmt_date(dt, self.sup["date_fmt"])

    # values as printed (or blank)
    def num(self):
        return self.inv["number"] if self.inv["number_mode"] == "printed" else ""

    def date(self):
        return self.d(self.inv["date"]) if self.inv["date_mode"] == "printed" else ""

    def due_text(self):
        inv = self.inv
        if inv["due_printed"] is not None:
            return self.d(inv["due_printed"])
        return inv["due_slot_text"]

    def meta(self, extra=()):
        """Ordered header fields for this invoice: (label, value)."""
        inv, L, out = self.inv, self.L, []
        if inv["number_mode"] != "omit":
            out.append((L["num"], self.num()))
        out.append((L["date"], self.date()))
        if self.due_text() is not None:
            out.append((L["due"], self.due_text()))
        if inv["terms_printed"] and self.sup["due_mode"] != "terms_in_due":
            out.append((L["terms"], inv["terms_printed"]))
        for e in extra:
            if e == "acct" and self.sup.get("acct"):
                out.append((L["acct"], self.sup["acct"]))
            elif e == "po" and inv["po"]:
                out.append((L["po"], inv["po"]))
        return out

    def qty(self, q):
        q = D(q)
        return f"{q:.2f}".rstrip("0").rstrip(".") if q != q.to_integral() else f"{int(q)}"


def draw_logo(pg, kind, x, y, s, col, initials="", inner=(1, 1, 1)):
    c = pg.c
    if not kind:
        return
    c.saveState()
    c.setFillColorRGB(*col)
    c.setStrokeColorRGB(*col)
    cx, cy = x + s / 2, y + s / 2
    if kind == "circle":
        c.circle(cx, cy, s / 2, stroke=0, fill=1)
        c.setFillColorRGB(*inner)
        c.setFont("Helvetica-Bold", s * 0.38)
        c.drawCentredString(cx, cy - s * 0.13, initials)
    elif kind == "mountain":
        p = c.beginPath()
        p.moveTo(x, y); p.lineTo(x + s * 0.38, y + s * 0.82); p.lineTo(x + s * 0.55, y + s * 0.5)
        p.lineTo(x + s * 0.7, y + s * 0.68); p.lineTo(x + s, y); p.close()
        c.drawPath(p, stroke=0, fill=1)
        c.setFillColorRGB(1, 1, 1)
        p = c.beginPath()
        p.moveTo(x + s * 0.29, y + s * 0.64); p.lineTo(x + s * 0.38, y + s * 0.82); p.lineTo(x + s * 0.47, y + s * 0.64)
        p.close()
        c.drawPath(p, stroke=0, fill=1)
    elif kind == "leaf":
        p = c.beginPath()
        p.moveTo(x + s * 0.1, y + s * 0.1)
        p.curveTo(x + s * 0.1, y + s * 0.8, x + s * 0.6, y + s, x + s * 0.95, y + s * 0.95)
        p.curveTo(x + s * 0.95, y + s * 0.4, x + s * 0.6, y + s * 0.05, x + s * 0.1, y + s * 0.1)
        c.drawPath(p, stroke=0, fill=1)
        c.setStrokeColorRGB(1, 1, 1)
        c.setLineWidth(s * 0.04)
        c.line(x + s * 0.12, y + s * 0.12, x + s * 0.8, y + s * 0.8)
    elif kind == "drop":
        p = c.beginPath()
        p.moveTo(cx, y + s)
        p.curveTo(cx + s * 0.1, y + s * 0.7, cx + s * 0.4, y + s * 0.5, cx + s * 0.4, y + s * 0.32)
        p.curveTo(cx + s * 0.4, y + s * 0.08, cx + s * 0.18, y, cx, y)
        p.curveTo(cx - s * 0.18, y, cx - s * 0.4, y + s * 0.08, cx - s * 0.4, y + s * 0.32)
        p.curveTo(cx - s * 0.4, y + s * 0.5, cx - s * 0.1, y + s * 0.7, cx, y + s)
        c.drawPath(p, stroke=0, fill=1)
    elif kind == "bolt":
        pts = [(0.6, 1), (0.15, 0.45), (0.45, 0.45), (0.35, 0), (0.85, 0.58), (0.55, 0.58)]
        p = c.beginPath()
        p.moveTo(x + pts[0][0] * s, y + pts[0][1] * s)
        for a, b in pts[1:]:
            p.lineTo(x + a * s, y + b * s)
        p.close()
        c.drawPath(p, stroke=0, fill=1)
    elif kind in ("hex", "gear"):
        nn = 6 if kind == "hex" else 20
        p = c.beginPath()
        for i in range(nn):
            r = s / 2 if (kind == "hex" or i % 2 == 0) else s * 0.40
            a = math.pi / 6 + i * 2 * math.pi / nn
            (p.moveTo if i == 0 else p.lineTo)(cx + r * math.cos(a), cy + r * math.sin(a))
        p.close()
        c.drawPath(p, stroke=0, fill=1)
        c.setFillColorRGB(*inner)
        if kind == "gear":
            c.circle(cx, cy, s * 0.16, stroke=0, fill=1)
        else:
            c.setFont("Helvetica-Bold", s * 0.34)
            c.drawCentredString(cx, cy - s * 0.12, initials)
    elif kind == "flake":
        c.setLineWidth(s * 0.07)
        c.setLineCap(1)
        for i in range(6):
            a = i * math.pi / 3
            ex, ey = cx + s * 0.48 * math.cos(a), cy + s * 0.48 * math.sin(a)
            c.line(cx, cy, ex, ey)
            mx, my = cx + s * 0.3 * math.cos(a), cy + s * 0.3 * math.sin(a)
            for da in (0.6, -0.6):
                c.line(mx, my, mx + s * 0.13 * math.cos(a + da), my + s * 0.13 * math.sin(a + da))
    elif kind == "barley":
        c.setLineWidth(s * 0.05)
        c.line(cx, y, cx, y + s * 0.95)
        for i in range(4):
            yy = y + s * (0.35 + i * 0.16)
            for sgn in (-1, 1):
                c.saveState()
                c.translate(cx + sgn * s * 0.12, yy)
                c.rotate(-sgn * 35)
                c.ellipse(-s * 0.07, -s * 0.12, s * 0.07, s * 0.12, stroke=0, fill=1)
                c.restoreState()
    elif kind == "bars":
        for i, hgt in enumerate((0.35, 0.55, 0.75, 1.0)):
            c.rect(x + i * s * 0.26, y, s * 0.2, s * hgt, stroke=0, fill=1)
    elif kind == "diamond":
        p = c.beginPath()
        p.moveTo(cx, y + s); p.lineTo(x + s, cy); p.lineTo(cx, y); p.lineTo(x, cy); p.close()
        c.drawPath(p, stroke=0, fill=1)
        c.setFillColorRGB(*inner)
        c.setFont("Helvetica-Bold", s * 0.3)
        c.drawCentredString(cx, cy - s * 0.1, initials)
    elif kind == "shield":
        p = c.beginPath()
        p.moveTo(x + s * 0.1, y + s * 0.9); p.lineTo(cx, y + s); p.lineTo(x + s * 0.9, y + s * 0.9)
        p.curveTo(x + s * 0.9, y + s * 0.4, x + s * 0.7, y + s * 0.15, cx, y)
        p.curveTo(x + s * 0.3, y + s * 0.15, x + s * 0.1, y + s * 0.4, x + s * 0.1, y + s * 0.9)
        c.drawPath(p, stroke=0, fill=1)
        c.setFillColorRGB(*inner)
        c.setFont("Helvetica-Bold", s * 0.36)
        c.drawCentredString(cx, cy - s * 0.08, initials)
    elif kind == "sun":
        c.circle(cx, cy, s * 0.24, stroke=0, fill=1)
        c.setLineWidth(s * 0.06)
        for i in range(12):
            a = i * math.pi / 6
            c.line(cx + s * 0.32 * math.cos(a), cy + s * 0.32 * math.sin(a),
                   cx + s * 0.48 * math.cos(a), cy + s * 0.48 * math.sin(a))
    elif kind == "flame":
        p = c.beginPath()
        p.moveTo(cx, y + s)
        p.curveTo(cx + s * 0.5, y + s * 0.6, cx + s * 0.45, y + s * 0.1, cx, y)
        p.curveTo(cx - s * 0.45, y + s * 0.1, cx - s * 0.4, y + s * 0.5, cx - s * 0.15, y + s * 0.7)
        p.curveTo(cx - s * 0.1, y + s * 0.5, cx, y + s * 0.6, cx, y + s)
        c.drawPath(p, stroke=0, fill=1)
    c.restoreState()


def initials(name):
    return "".join(w[0] for w in re.findall(r"[A-Za-z]+", name) if w[0].isupper() and w not in ("LLC",))[:3]


def lighten(col, k):
    return tuple(1 - (1 - v) * k for v in col)


def items_table(pg, x, y, cols, lines, size=8.5, row_h=15, header=True, hfill=None, htext=None, hw="b",
                grid=False, zebra=None, rules=True, upper=False, min_rows=0, rule_col=None, hsize=None,
                vlines=False, bottom=None, w="r", pad=4):
    """cols: list of (header, width, align, fn(line)->str). Returns y below the table."""
    tw = sum(cw for _, cw, _, _ in cols)
    rc = rule_col or (0.65, 0.65, 0.65)
    top = y
    if header:
        if hfill:
            pg.rect(x, y - row_h, tw, row_h, fill=hfill)
        cx = x
        for h, cw, al, _ in cols:
            h = h.upper() if upper else h
            tx = cx + pad if al == "l" else cx + cw - pad if al == "r" else cx + cw / 2
            pg.t(tx, y - row_h + 4.5, h, hsize or size, hw, al, htext)
            cx += cw
        y -= row_h
        if grid:
            pg.line(x, y, x + tw, y, 0.6, rc)
        if rules and not hfill:
            pg.line(x, y, x + tw, y, 0.8, rc)
    n = max(len(lines), min_rows)
    for i in range(n):
        ln = lines[i] if i < len(lines) else None
        cells = []
        if ln is not None:
            for h, cw, al, fn in cols:
                cells.append(pg.wrap(fn(ln), cw - 2 * pad, size, w) if al == "l" else [fn(ln)])
        nl = max([len(cl) for cl in cells] or [1])
        rh = row_h + (nl - 1) * (size + 2)
        if zebra and i % 2 == 1:
            pg.rect(x, y - rh, tw, rh, fill=zebra)
        cx = x
        for j, (h, cw, al, fn) in enumerate(cols):
            if ln is not None:
                for k, s in enumerate(cells[j]):
                    tx = cx + pad if al == "l" else cx + cw - pad if al == "r" else cx + cw / 2
                    pg.t(tx, y - row_h + 4.5 - k * (size + 2), s, size, w, al)
            cx += cw
        y -= rh
        if grid or (rules and ln is not None and not zebra):
            pg.line(x, y, x + tw, y, 0.4 if not grid else 0.6, rc)
    if bottom is not None and y > bottom:
        y = bottom
    if grid or vlines:
        cx = x
        for _, cw, _, _ in cols:
            pg.line(cx, top, cx, y, 0.6, rc)
            cx += cw
        pg.line(cx, top, cx, y, 0.6, rc)
        if grid:
            pg.line(x, top, x + tw, top, 0.6, rc)
        pg.line(x, y, x + tw, y, 0.6, rc)
    return y


def totals(pg, xl, xr, y, size=9.5, lh=15, w="r", lalign="r", fill_last=None, upper=False,
           line_above_last=False):
    rows = pg.inv["total_rows"]
    for i, r in enumerate(rows):
        last = i == len(rows) - 1
        lab = r["label"].upper() if upper else r["label"]
        val = "" if r["amt"] is None else pg.m(r["amt"])
        sz = size + (1 if last else 0)
        if last and fill_last:
            x0 = xl - pg.sw(lab, sz, "b") - 10 if lalign == "r" else xl - 6
            pg.rect(x0, y - 5.5, xr - x0 + 6, lh + 2, fill=fill_last)
        if last and line_above_last:
            x0 = min(xl - pg.sw(lab, sz, "b"), xr - 160) if lalign == "r" else xl
            pg.line(x0, y + lh - 4, xr, y + lh - 4, 0.8)
        pg.t(xl, y, lab, sz, "b" if last else w, lalign)
        pg.t(xr, y, val, sz, "b" if last else w, "r")
        y -= lh
    return y


def addr_lines(pg, x, y, title, lines, size=9, lh=11.5, tsize=None, tw="b", tcol=None, w="r", upper=False,
               col=None):
    if title:
        pg.t(x, y, title.upper() if upper else title, tsize or size, tw, "l", tcol)
        y -= lh + 1
    for ln in lines:
        pg.t(x, y, ln.upper() if upper else ln, size, w, "l", col)
        y -= lh
    return y


def bill_to(inv, attn=True):
    out = [CUSTOMER["name"]]
    if attn:
        out.append(CUSTOMER["attn"])
    return out + CUSTOMER["addr"]


def ship_to(inv):
    return list(inv["site"])


def supplier_contact(sup, sep=False):
    bits = [sup["phone"]]
    if sup.get("email"):
        bits.append(sup["email"])
    if sup.get("web"):
        bits.append(sup["web"])
    return bits


# ----------------------------------------------------------------------------- layouts
def L_classic(pg):
    s, inv, W, H = pg.sup, pg.inv, pg.W, pg.H
    col = s["color"]
    draw_logo(pg, s["logo"], 40, H - 96, 48, col, initials(s["name"]))
    pg.t(98, H - 62, s["name"], 16, "b", "l")
    y = addr_lines(pg, 98, H - 76, None, s["addr"] + [s["phone"]], 8.5, 10.5)
    if s.get("web"):
        pg.t(98, y, s["web"], 8.5, "r", "l", col)
    pg.t(W - 40, H - 64, "INVOICE", 24, "b", "r", col)
    y = H - 88
    for lab, val in pg.meta(extra=("acct", "po")):
        pg.t(W - 140, y, lab + ":", 8.5, "b", "r")
        pg.t(W - 132, y, val, 8.5, "r", "l")
        y -= 12
    yb = min(y, H - 160) - 10
    pg.rect(40, yb - 70, 250, 70, stroke=(0.7, 0.7, 0.7))
    pg.rect(40, yb - 14, 250, 14, fill=lighten(col, 0.2))
    pg.t(46, yb - 10, "BILL TO", 8, "b", "l", col)
    addr_lines(pg, 46, yb - 26, None, bill_to(inv), 8.5, 10.5)
    pg.rect(322, yb - 70, 250, 70, stroke=(0.7, 0.7, 0.7))
    pg.rect(322, yb - 14, 250, 14, fill=lighten(col, 0.2))
    pg.t(328, yb - 10, "SERVICE LOCATION" if not s.get("rental") else "JOB SITE", 8, "b", "l", col)
    addr_lines(pg, 328, yb - 26, None, ship_to(inv), 8.5, 10.5)
    y = yb - 92
    if s.get("rental"):
        pg.t(40, y + 6, f"Rental out: {pg.d(inv['order_date'])}    Returned: {pg.d(inv['ship_date'])}", 8.5, "r", "l")
        y -= 10
    cols = [("Description", 300, "l", lambda l: l["desc"]),
            ("Qty", 50, "r", lambda l: pg.qty(l["qty"])),
            ("Rate", 80, "r", lambda l: pg.im(l["price"])),
            ("Amount", 102, "r", lambda l: pg.im(l["amount"]))]
    y = items_table(pg, 40, y, cols, inv["lines"], 9, 18, hfill=col, htext=(1, 1, 1), rules=True)
    y = totals(pg, W - 150, W - 44, y - 22, 9.5, 15)
    yn = 120
    for n in s["notes"]:
        for ln in pg.wrap(n, 300, 8):
            pg.t(40, yn, ln, 8, "i", "l", (0.3, 0.3, 0.3))
            yn -= 10
    pg.line(40, 60, W - 40, 60, 0.5, (0.7, 0.7, 0.7))
    pg.t(40, 46, f"Remit to: {s['legal']}, {', '.join(s['addr'])}", 7.5, "r", "l", (0.35, 0.35, 0.35))
    pg.t(W - 40, 46, "Page 1 of 1", 7.5, "r", "r", (0.35, 0.35, 0.35))


def L_banded(pg):
    s, inv, W, H = pg.sup, pg.inv, pg.W, pg.H
    col = s["color"]
    pg.rect(0, H - 96, W, 96, fill=col)
    draw_logo(pg, s["logo"], 36, H - 78, 56, (1, 1, 1), initials(s["name"]), inner=col)
    pg.t(104, H - 50, s["name"], 22, "b", "l", (1, 1, 1))
    pg.t(104, H - 68, s.get("tagline", ""), 9.5, "i", "l", lighten((1, 1, 1), 0.85))
    y = H - 34
    for ln in s["addr"] + supplier_contact(s):
        pg.t(W - 36, y, ln, 8, "r", "r", (1, 1, 1))
        y -= 10
    pg.t(36, H - 132, "INVOICE", 20, "b", "l", col, cs=3)
    meta = pg.meta(extra=("po", "acct"))
    bx, bw = 36, (W - 72) / max(4, len(meta))
    for i, (lab, val) in enumerate(meta):
        x = bx + i * bw
        pg.rect(x + 2, H - 186, bw - 4, 38, fill=lighten(col, 0.12))
        pg.t(x + 10, H - 162, lab.upper(), 7, "b", "l", col)
        pg.t(x + 10, H - 178, val, 10, "b", "l")
    y = H - 212
    addr_lines(pg, 36, y, "Billed to", bill_to(inv), 9, 11.5, tsize=8, tcol=col)
    addr_lines(pg, 300, y, "Deliver to", ship_to(inv), 9, 11.5, tsize=8, tcol=col)
    y -= 78
    cols = [("Item", 70, "l", lambda l: l["sku"]), ("Description", 238, "l", lambda l: l["desc"]),
            ("Qty", 50, "r", lambda l: pg.qty(l["qty"])), ("Unit", 44, "c", lambda l: l["unit"]),
            ("Price", 64, "r", lambda l: pg.im(l["price"])), ("Total", 74, "r", lambda l: pg.im(l["amount"]))]
    y = items_table(pg, 36, y, cols, inv["lines"], 8.5, 18, hfill=col, htext=(1, 1, 1),
                    zebra=lighten(col, 0.08), rules=False)
    pg.line(36, y, W - 36, y, 0.8, col)
    y = totals(pg, W - 170, W - 44, y - 20, 9.5, 16, fill_last=lighten(col, 0.25))
    yn = max(min(y - 20, 150), 110)
    pg.t(36, yn, "Notes", 8, "b", "l", col)
    for n in s["notes"]:
        yn -= 11
        pg.t(36, yn, n, 8, "r", "l")
    pg.rect(0, 0, W, 40, fill=lighten(col, 0.15))
    pg.t(36, 22, f"Remit to: {s['legal']}  |  {', '.join(s['addr'])}", 8, "r", "l", col)
    pg.t(36, 11, "Please reference the invoice number with your payment.", 7, "i", "l", col)


def L_centered(pg):
    s, inv, W, H = pg.sup, pg.inv, pg.W, pg.H
    col = s["color"]
    pg.t(W / 2, H - 62, s["name"], 22, "b", "c", col)
    pg.t(W / 2, H - 78, "   ·   ".join(s["addr"] + [s["phone"]]), 8.5, "r", "c")
    if s.get("web"):
        pg.t(W / 2, H - 90, s["web"], 8.5, "i", "c")
    pg.line(54, H - 100, W - 54, H - 100, 1.2, col)
    pg.line(54, H - 103, W - 54, H - 103, 0.4, col)
    pg.t(W / 2, H - 132, "INVOICE", 15, "b", "c", col, cs=6)
    y = H - 166
    addr_lines(pg, 60, y, "Sold To:", bill_to(inv, attn=False), 10, 12.5, tw="b")
    yy = y
    for lab, val in pg.meta(extra=("po",)):
        pg.t(W - 200, yy, lab + ":", 10, "b", "l")
        pg.t(W - 60, yy, val, 10, "r", "r")
        yy -= 14
    y = min(y - 62, yy) - 20
    cols = [("Qty", 50, "c", lambda l: pg.qty(l["qty"])), ("Description", 298, "l", lambda l: l["desc"]),
            ("Unit Price", 76, "r", lambda l: pg.im(l["price"])), ("Amount", 80, "r", lambda l: pg.im(l["amount"]))]
    y = items_table(pg, 54, y, cols, inv["lines"], 10, 18, grid=True, hfill=lighten(col, 0.12),
                    min_rows=max(6, len(inv["lines"]) + 1), rule_col=(0.35, 0.35, 0.35))
    rows = inv["total_rows"]
    xr = W - 54
    for i, r in enumerate(rows):
        pg.rect(xr - 196, y - 18, 196, 18, stroke=(0.35, 0.35, 0.35), lw=0.6)
        pg.line(xr - 80, y, xr - 80, y - 18, 0.6, (0.35, 0.35, 0.35))
        b = "b" if i == len(rows) - 1 else "r"
        pg.t(xr - 84, y - 13, r["label"], 10, b, "r")
        pg.t(xr - 4, y - 13, "" if r["amt"] is None else pg.m(r["amt"]), 10, b, "r")
        y -= 18
    yn = 128
    for n in s["notes"]:
        pg.t(W / 2, yn, n, 9, "i", "c")
        yn -= 12
    if s["legal"] != s["name"] or True:
        pg.t(W / 2, 70, f"Make checks payable to {s['legal']}", 8.5, "r", "c")


def L_sidebar(pg):
    s, inv, W, H = pg.sup, pg.inv, pg.W, pg.H
    col = s["color"]
    sw = 160
    pg.rect(0, 0, sw, H, fill=col)
    draw_logo(pg, s["logo"], 24, H - 96, 56, (1, 1, 1), initials(s["name"]), inner=col)
    y = H - 122
    for ln in pg.wrap(s["name"], sw - 40, 14, "b"):
        pg.t(24, y, ln, 14, "b", "l", (1, 1, 1))
        y -= 17
    y -= 10
    for ln in s["addr"] + supplier_contact(s):
        pg.t(24, y, ln, 7.5, "r", "l", (1, 1, 1))
        y -= 10.5
    pg.t(24, 160, "REMIT PAYMENT TO", 7, "b", "l", lighten((1, 1, 1), 0.8))
    addr_lines(pg, 24, 147, None, [s["legal"]] + s["addr"], 7.5, 10, col=(1, 1, 1))
    x0 = sw + 30
    pg.t(x0, H - 70, "Invoice", 30, "r", "l", col)
    meta = pg.meta(extra=("po", "acct"))
    for i, (lab, val) in enumerate(meta):
        cx = x0 + (i % 3) * 135
        cy = H - 108 - (i // 3) * 34
        pg.t(cx, cy, lab.upper(), 6.5, "b", "l", (0.45, 0.45, 0.45))
        pg.t(cx, cy - 13, val, 10, "r", "l")
    y = H - 108 - ((len(meta) + 2) // 3) * 34 - 12
    addr_lines(pg, x0, y, "BILL TO", bill_to(inv), 9, 11.5, tsize=6.5, tcol=(0.45, 0.45, 0.45))
    addr_lines(pg, x0 + 210, y, "SITE", ship_to(inv), 9, 11.5, tsize=6.5, tcol=(0.45, 0.45, 0.45))
    y -= 80
    cols = [("Description", 222, "l", lambda l: l["desc"]), ("Qty", 46, "r", lambda l: pg.qty(l["qty"])),
            ("Rate", 70, "r", lambda l: pg.im(l["price"])), ("Amount", 84, "r", lambda l: pg.im(l["amount"]))]
    y = items_table(pg, x0, y, cols, inv["lines"], 9, 20, rules=True, hw="b", hsize=7.5, upper=True,
                    rule_col=(0.8, 0.8, 0.8))
    y = totals(pg, W - 150, W - 34, y - 22, 9.5, 16, line_above_last=True)
    yn = 90
    for n in s["notes"]:
        pg.t(x0, yn, n, 8, "i", "l", (0.4, 0.4, 0.4))
        yn -= 11
    if s["key"] == "thimbleworth":
        pg.t(x0, 104, f"Payment terms: {inv['terms_printed']}", 8.5, "b", "l")


def L_service_ticket(pg):
    s, inv, W, H = pg.sup, pg.inv, pg.W, pg.H
    col = s["color"]
    draw_logo(pg, s["logo"], 36, H - 88, 46, col, initials(s["name"]))
    pg.t(90, H - 56, s["name"], 17, "b", "l", col)
    pg.t(90, H - 70, " | ".join(s["addr"]), 8.5, "r", "l")
    pg.t(90, H - 81, " | ".join(supplier_contact(s)), 8.5, "r", "l")
    pg.rect(W - 196, H - 92, 160, 50, stroke=col, lw=1.2)
    pg.t(W - 116, H - 58, "SERVICE INVOICE", 11, "b", "c", col)
    if inv["number_mode"] != "omit":
        pg.t(W - 188, H - 80, pg.L["num"] + ":", 9, "b", "l")
        pg.t(W - 44, H - 80, pg.num(), 11, "b", "r", (0.7, 0.1, 0.1))
    # info grid
    y = H - 112
    meta = [(l, v) for l, v in pg.meta() if l != pg.L["num"]]
    cells = meta + [("Work Order", inv["wo"]), ("Technician", inv["tech"])]
    if inv["po"]:
        cells.append(("Customer PO", inv["po"]))
    cw = (W - 72) / 3
    for i, (lab, val) in enumerate(cells):
        x = 36 + (i % 3) * cw
        yy = y - (i // 3) * 30
        pg.rect(x, yy - 28, cw, 28, stroke=(0.5, 0.5, 0.5))
        pg.t(x + 4, yy - 9, lab.upper(), 6.5, "b", "l", col)
        pg.t(x + 4, yy - 22, val, 9.5, "r", "l")
    y -= ((len(cells) + 2) // 3) * 30 + 12
    pg.rect(36, y - 62, (W - 72) / 2 - 4, 62, stroke=(0.5, 0.5, 0.5))
    addr_lines(pg, 42, y - 11, "CUSTOMER", bill_to(inv, attn=False), 8.5, 10.5, tsize=6.5, tcol=col)
    pg.rect(W / 2 + 4, y - 62, (W - 72) / 2 - 4, 62, stroke=(0.5, 0.5, 0.5))
    addr_lines(pg, W / 2 + 10, y - 11, "JOB LOCATION", ship_to(inv) + [CUSTOMER["phone"]], 8.5, 10.5, tsize=6.5, tcol=col)
    y -= 76
    pg.t(36, y, "DESCRIPTION OF WORK PERFORMED", 7.5, "b", "l", col)
    pg.rect(36, y - 40, W - 72, 34, stroke=(0.5, 0.5, 0.5))
    for k, ln in enumerate(pg.wrap(inv["work"], W - 84, 9)[:2]):
        pg.t(42, y - 18 - k * 11, ln, 9, "i", "l")
    y -= 56
    labor = [l for l in inv["lines"] if l["kind"] == "labor"]
    other = [l for l in inv["lines"] if l["kind"] != "labor"]
    pg.t(36, y, "LABOR", 7.5, "b", "l", col)
    cols = [("Description", 300, "l", lambda l: l["desc"]), ("Hours", 60, "r", lambda l: pg.qty(l["qty"])),
            ("Rate", 80, "r", lambda l: pg.im(l["price"])), ("Amount", 100, "r", lambda l: pg.im(l["amount"]))]
    y = items_table(pg, 36, y - 4, cols, labor, 8.5, 15, grid=True, hfill=lighten(col, 0.12), min_rows=2,
                    rule_col=(0.5, 0.5, 0.5))
    y -= 14
    pg.t(36, y, "PARTS / MATERIALS / CHARGES", 7.5, "b", "l", col)
    cols = [("Qty", 40, "r", lambda l: pg.qty(l["qty"])), ("Part #", 70, "l", lambda l: l["sku"]),
            ("Description", 250, "l", lambda l: l["desc"]),
            ("Price", 80, "r", lambda l: pg.im(l["price"])), ("Amount", 100, "r", lambda l: pg.im(l["amount"]))]
    y = items_table(pg, 36, y - 4, cols, other, 8.5, 15, grid=True, hfill=lighten(col, 0.12),
                    min_rows=max(4, len(other)), rule_col=(0.5, 0.5, 0.5))
    lab_t = sum((l["amount"] for l in labor), D(0))
    par_t = sum((l["amount"] for l in other), D(0))
    y -= 16
    yt = y
    pg.t(W - 150, yt, "Labor", 9, "r", "r")
    pg.t(W - 40, yt, pg.m(lab_t), 9, "r", "r")
    pg.t(W - 150, yt - 14, "Parts & Charges", 9, "r", "r")
    pg.t(W - 40, yt - 14, pg.m(par_t), 9, "r", "r")
    pg.line(W - 230, yt - 20, W - 36, yt - 20, 0.5)
    totals(pg, W - 150, W - 40, yt - 34, 9.5, 15)
    pg.t(36, yt, "Work completed to my satisfaction:", 8, "r", "l")
    pg.line(36, yt - 30, 260, yt - 30, 0.6)
    pg.t(36, yt - 40, "Customer signature", 7, "r", "l", (0.4, 0.4, 0.4))
    pg.t(36, 50, "Thank you! Past-due balances subject to 1.5% monthly finance charge.", 7.5, "i", "l")


def L_dotmatrix(pg):
    s, inv, W, H = pg.sup, pg.inv, pg.W, pg.H
    col = s["color"]
    rng = random.Random(inv["rng_seed"])
    if s.get("greenbar"):
        for i in range(0, int(H), 36):
            pg.rect(28, i, W - 56, 18, fill=(0.88, 0.95, 0.88))
        for yy in range(18, int(H), 36):
            pg.c.setFillColorRGB(1, 1, 1)
            pg.c.setStrokeColorRGB(0.7, 0.7, 0.7)
            pg.c.circle(14, yy, 4.5, stroke=1, fill=1)
            pg.c.circle(W - 14, yy, 4.5, stroke=1, fill=1)
    # preprinted form parts (sans, colored)
    draw_logo(pg, s["logo"], 44, H - 84, 40, col, initials(s["name"]))
    pg.t(94, H - 56, s["name"], 15, "Helvetica-Bold", "l", col)
    pg.t(94, H - 70, "  ".join(s["addr"]), 8, "Helvetica", "l", col)
    pg.t(94, H - 80, s["phone"] + (("   " + s["web"]) if s.get("web") else ""), 8, "Helvetica", "l", col)
    pg.t(W - 44, H - 58, "INVOICE", 18, "Helvetica-Bold", "r", col)
    # data printed by an impact printer, slightly off the form grid
    ox, oy = rng.uniform(-3, 3), rng.uniform(-3, 2)
    cw = pdfmetrics.stringWidth("0", "Courier", 9)

    def P(x, y, txt, size=9, w="Courier"):
        pg.t(x + ox, y + oy, txt, size, w, "l")

    y = H - 110
    meta = pg.meta(extra=("acct", "po"))
    for i, (lab, val) in enumerate(meta):
        P(44 + (i % 2) * 270, y - (i // 2) * 12, f"{lab.upper():<26}{val}" if len(lab) > 14 else f"{lab.upper()+':':<14}{val}")
    y -= ((len(meta) + 1) // 2) * 12 + 14
    if s["key"] == "redtail":
        P(44, y, f"BOL NO: {inv['bol']}    SHIP DATE: {pg.d(inv['ship_date'])}    ORIGIN: BOULDER CO")
        y -= 16
    P(44, y, "SOLD TO:")
    P(320, y, "SHIP TO:" if s["key"] != "redtail" else "CONSIGNEE:")
    for k, (a, b) in enumerate(zip(bill_to(inv), ship_to(inv) + [""])):
        P(44, y - 12 * (k + 1), a.upper())
        P(320, y - 12 * (k + 1), b.upper())
    y -= 70
    bo = s.get("bo")
    hdr = (f"{'ITEM':<8}{'DESCRIPTION':<30}{'ORD':>5}{'SHP':>5}{'B/O':>5} {'UM':<4}{'PRICE':>9}{'EXTENSION':>11}"
           if bo else f"{'ITEM':<8}{'DESCRIPTION':<36}{'QTY':>8} {'UM':<5}{'PRICE':>9}{'AMOUNT':>11}")
    P(44, y, "-" * len(hdr))
    P(44, y - 11, hdr)
    P(44, y - 22, "-" * len(hdr))
    y -= 36
    for ln in inv["lines"]:
        q = pg.qty(ln["qty"])
        pr = f"{ln['price']:.2f}" if ln["price"] >= 1 else f"{ln['price']:.3f}"
        if bo:
            bo_q = rng.choice([0, 0, 0, 1])
            row = f"{ln['sku'][:7]:<8}{ln['desc'][:29]:<30}{int(ln['qty']) + bo_q:>5}{q:>5}{bo_q:>5} {ln['unit']:<4}{pr:>9}{pg.im(ln['amount']):>11}"
        else:
            row = f"{ln['sku'][:7]:<8}{ln['desc'][:35]:<36}{q:>8} {ln['unit']:<5}{pr:>9}{pg.im(ln['amount']):>11}"
        P(44, y, row)
        y -= 12
    P(44, y - 2, "-" * len(hdr))
    y -= 20
    rw = len(hdr)
    for r in inv["total_rows"]:
        v = "" if r["amt"] is None else pg.m(r["amt"])
        P(44, y, f"{r['label'].upper():>{rw - 14}}{v:>14}", 9, "Courier-Bold" if r["kind"] in ("total", "due") else "Courier")
        y -= 12
    yn = 96
    for n in s["notes"]:
        P(44, yn, n.upper())
        yn -= 11
    pg.t(44, 60, f"REMIT TO: {s['legal'].upper()}  {'  '.join(s['addr']).upper()}", 7.5, "Helvetica", "l", col)


def L_minimal(pg):
    s, inv, W, H = pg.sup, pg.inv, pg.W, pg.H
    col = s["color"]
    grey = (0.45, 0.45, 0.48)
    draw_logo(pg, s["logo"], 56, H - 86, 26, col, initials(s["name"]))
    pg.t(90, H - 76, s["name"], 13, "b", "l")
    pg.t(W - 56, H - 80, "Invoice", 26, "r", "r", (0.6, 0.6, 0.62))
    meta = pg.meta(extra=("po",))
    x = 56
    y = H - 140
    for lab, val in meta:
        pg.t(x, y, lab, 7.5, "r", "l", grey)
        pg.t(x, y - 14, val, 10, "b" if lab == pg.L["num"] else "r", "l")
        x += max(100, pg.sw(val, 10) + 30)
    y -= 50
    addr_lines(pg, 56, y, "Billed to", bill_to(inv), 9, 12, tsize=7.5, tw="r", tcol=grey)
    addr_lines(pg, 230, y, "From", [s["name"]] + s["addr"] + [s["phone"]], 9, 12, tsize=7.5, tw="r", tcol=grey)
    # big amount
    last = inv["total_rows"][-1]
    if last["amt"] is not None:
        pg.t(W - 56, y - 2, last["label"], 7.5, "r", "r", grey)
        pg.t(W - 56, y - 28, pg.m(last["amt"]), 22, "b", "r", col)
        if inv["due_printed"]:
            pg.t(W - 56, y - 44, f"{pg.L['due']} {pg.d(inv['due_printed'])}", 8, "r", "r", grey)
    y -= 100
    cols = [("Description", 290, "l", lambda l: l["desc"]), ("Qty", 50, "r", lambda l: pg.qty(l["qty"])),
            ("Unit price", 76, "r", lambda l: pg.im(l["price"])), ("Amount", 84, "r", lambda l: pg.im(l["amount"]))]
    y = items_table(pg, 56, y, cols, inv["lines"], 9, 22, hw="r", hsize=7.5, rule_col=(0.88, 0.88, 0.9))
    y = totals(pg, W - 140, W - 60, y - 24, 9.5, 18, line_above_last=True)
    pg.line(56, 92, W - 56, 92, 0.4, (0.85, 0.85, 0.85))
    yn = 78
    for n in s["notes"]:
        pg.t(56, yn, n, 7.5, "r", "l", grey)
        yn -= 10
    pg.t(W - 56, 78, s.get("web") or "", 7.5, "r", "r", grey)


def L_counter_receipt(pg):
    s, inv, W, H = pg.sup, pg.inv, pg.W, pg.H
    rng = random.Random(inv["rng_seed"])
    mono = s["font"] == "courier"
    fr, fb = (pg.f["r"], pg.f["b"])
    sz = 8.2 if mono else 8.0
    y = H - 34
    pg.t(W / 2, y, s["name"], 10.5, fb, "c")
    y -= 12
    for ln in s["addr"] + [s["phone"]]:
        pg.t(W / 2, y, ln, sz, fr, "c")
        y -= 10
    y -= 6
    pg.t(W / 2, y, "CHARGE INVOICE", 10, fb, "c")
    y -= 16
    xl, xr = 20, W - 20
    meta = pg.meta(extra=("acct", "po"))
    for lab, val in meta:
        pg.t(xl, y, f"{lab}:", sz, fb, "l")
        pg.t(xl + 70, y, val, sz, fr, "l")
        y -= 10.5
    pg.t(xl, y, f"TIME: {inv['time']}   CLERK: {inv['clerk']}   REG: {rng.randint(1, 4)}", sz, fr, "l")
    y -= 14
    pg.t(xl, y, "SOLD TO: " + CUSTOMER["name"].upper(), sz, fr, "l")
    y -= 10
    pg.t(xl, y, "        " + CUSTOMER["addr"][0].upper(), sz, fr, "l")
    y -= 12
    pg.line(xl, y, xr, y, 0.6, dash=([2, 2], 0))
    y -= 11
    for ln in inv["lines"]:
        pg.t(xl, y, f"{ln['sku']}", sz, fr, "l")
        pg.t(xr, y, pg.im(ln["amount"]) + (" T" if ln["tax"] else "  "), sz, fr, "r")
        y -= 9.5
        pg.t(xl + 8, y, f"{ln['desc']}", sz - 0.4, fr, "l")
        y -= 9.5
        pg.t(xl + 8, y, f"{pg.qty(ln['qty'])} {ln['unit']} @ {pg.im(ln['price'])}", sz - 0.4, fr, "l")
        y -= 11
    pg.line(xl, y + 3, xr, y + 3, 0.6, dash=([2, 2], 0))
    y -= 10
    for r in inv["total_rows"]:
        big = r["kind"] in ("total", "due")
        pg.t(xr - 70, y, r["label"], sz + (1 if big else 0), fb if big else fr, "r")
        pg.t(xr, y, "" if r["amt"] is None else pg.m(r["amt"]), sz + (1 if big else 0), fb if big else fr, "r")
        y -= 12
    y -= 8
    for n in s["notes"]:
        pg.t(W / 2, y, n, sz, fr, "c")
        y -= 10
    y -= 14
    pg.t(xl, y, "RECEIVED BY:", sz, fr, "l")
    pg.line(xl + 62, y - 1, xr, y - 1, 0.5)
    y -= 46
    x = 40
    while x < W - 40:
        bw = rng.choice([0.8, 1.2, 1.8, 2.6])
        pg.rect(x, y, bw, 26, fill=(0, 0, 0))
        x += bw + rng.choice([0.8, 1.4, 2.0])
    pg.t(W / 2, y - 10, pg.num() or "", sz - 1, fr, "c")


def L_quickbooks(pg):
    s, inv, W, H = pg.sup, pg.inv, pg.W, pg.H
    grey = (0.85, 0.85, 0.85)
    stroke = (0.2, 0.2, 0.2)
    draw_logo(pg, s["logo"], 36, H - 84, 40, s["color"], initials(s["name"]))
    pg.t(84, H - 54, s["name"], 13, "b", "l")
    addr_lines(pg, 84, H - 67, None, s["addr"] + [s["phone"]], 8.5, 10)
    pg.t(W - 36, H - 56, "Invoice", 22, "b", "r")
    hdr = [("Date", pg.date())]
    if s.get("qb_due") and pg.due_text() is not None:
        hdr.append((pg.L["due"], pg.due_text()))
    if inv["number_mode"] != "omit":
        hdr.append((pg.L["num"], pg.num()))
    bw = 78
    x0 = W - 36 - bw * len(hdr)
    for i, (lab, val) in enumerate(hdr):
        x = x0 + i * bw
        pg.rect(x, H - 92, bw, 14, fill=grey, stroke=stroke)
        pg.rect(x, H - 110, bw, 18, stroke=stroke)
        pg.t(x + bw / 2, H - 88, lab, 8, "r", "c")
        pg.t(x + bw / 2, H - 104, val, 8.5, "r", "c")
    for j, (title, lines) in enumerate((("Bill To", bill_to(inv)), ("Ship To", ship_to(inv)))):
        x = 36 + j * 280
        pg.rect(x, H - 144, 250, 14, fill=grey, stroke=stroke)
        pg.rect(x, H - 212, 250, 68, stroke=stroke)
        pg.t(x + 4, H - 140, title, 8, "r", "l")
        addr_lines(pg, x + 6, H - 157, None, lines, 8.5, 10.5)
    strip = []
    strip.append(("P.O. No.", inv["po"] or ""))
    if inv["terms_printed"] and s["due_mode"] != "terms_in_due":
        strip.append((pg.L["terms"], inv["terms_printed"]))
    if not s.get("qb_due") and inv["due_printed"]:
        strip.append((pg.L["due"], pg.d(inv["due_printed"])))
    strip += [("Rep", inv["rep"]), ("Ship", pg.d(inv["ship_date"])), ("Via", inv["via"]), ("F.O.B.", "Origin")]
    sw_ = (W - 72) / len(strip)
    for i, (lab, val) in enumerate(strip):
        x = 36 + i * sw_
        pg.rect(x, H - 240, sw_, 14, fill=grey, stroke=stroke)
        pg.rect(x, H - 258, sw_, 18, stroke=stroke)
        pg.t(x + sw_ / 2, H - 236, lab, 8, "r", "c")
        pg.t(x + sw_ / 2, H - 252, val, 8.5, "r", "c")
    cols = [("Quantity", 56, "r", lambda l: pg.qty(l["qty"])), ("Item Code", 74, "l", lambda l: l["sku"]),
            ("Description", 254, "l", lambda l: l["desc"]),
            ("Price Each", 74, "r", lambda l: pg.im(l["price"])), ("Amount", 82, "r", lambda l: pg.im(l["amount"]))]
    nrows = len(inv["total_rows"])
    bottom = 70 + nrows * 18
    y = items_table(pg, 36, H - 270, cols, inv["lines"], 8.5, 16, hfill=grey, hw="r", rules=False,
                    vlines=True, bottom=bottom, rule_col=stroke)
    pg.rect(36, H - 286, W - 72, 16, stroke=stroke)
    y = bottom
    pg.t(40, y - 14, s["notes"][0] if s["notes"] else "", 8, "i", "l")
    for r in inv["total_rows"]:
        pg.rect(W - 236, y - 18, 200, 18, stroke=stroke)
        b = "b" if r["kind"] in ("total", "due") else "r"
        pg.t(W - 230, y - 13, r["label"], 9, b, "l")
        pg.t(W - 40, y - 13, "" if r["amt"] is None else pg.m(r["amt"]), 9, b, "r")
        y -= 18
    if s.get("web"):
        pg.t(40, 50, f"{s['phone']}   {s['web']}", 7.5, "r", "l", (0.3, 0.3, 0.3))


def L_boxed_form(pg):
    s, inv, W, H = pg.sup, pg.inv, pg.W, pg.H
    col = s["color"]
    ink = (0.10, 0.18, 0.55)  # ballpoint
    rng = random.Random(inv["rng_seed"])

    def hand(x, y, txt, size=12, align="l"):
        if txt:
            pg.t(x + rng.uniform(-1.5, 1.5), y + rng.uniform(-1.2, 1.2), txt, size - 1, "Times-Italic", align, ink)

    pg.rect(28, 28, W - 56, H - 56, stroke=col, lw=1.4)
    draw_logo(pg, s["logo"], 44, H - 92, 44, col, initials(s["name"]))
    pg.t(98, H - 62, s["name"], 19, "Times-Bold", "l", col)
    pg.t(98, H - 76, "  •  ".join(s["addr"]), 8.5, "Helvetica", "l", col)
    pg.t(98, H - 87, f"Phone {s['phone']}", 8.5, "Helvetica", "l", col)
    pg.t(W - 44, H - 60, "INVOICE", 15, "Helvetica-Bold", "r", col)
    if inv["number_mode"] != "omit":
        pg.t(W - 140, H - 84, "No.", 11, "Helvetica-Bold", "l", col)
        if inv["number_mode"] == "printed":
            pg.t(W - 44, H - 85, pg.num(), 15, "Courier-Bold", "r", (0.80, 0.12, 0.12))

    def box(x, y, w, h, label, val=None, vs=12, align="l"):
        pg.rect(x, y - h, w, h, stroke=col, lw=0.8)
        pg.t(x + 3, y - 8, label, 6, "Helvetica-Bold", "l", col)
        if val is not None:
            hand(x + 8 if align == "l" else x + w - 6, y - h + 6, val, vs, align)

    y = H - 104
    box(44, y, 160, 30, "DATE", pg.date() if inv["date_mode"] == "printed" else "")
    if s["due_mode"] == "terms_in_due" or inv["due_printed"]:
        box(204, y, 170, 30, "DUE DATE", pg.due_text())
        if inv["terms_printed"] and s["due_mode"] != "terms_in_due":
            box(374, y, W - 418, 30, "TERMS", inv["terms_printed"])
        else:
            box(374, y, W - 418, 30, "CUSTOMER ORDER NO.", inv["po"] or "")
    else:
        box(204, y, 170, 30, "TERMS", inv["terms_printed"] or "")
        box(374, y, W - 418, 30, "CUSTOMER ORDER NO.", inv["po"] or "")
    y -= 30
    box(44, y, W - 88, 30, "NAME", CUSTOMER["name"])
    y -= 30
    box(44, y, W - 238, 30, "ADDRESS", ", ".join(CUSTOMER["addr"]))
    box(W - 194, y, 150, 30, "PHONE", CUSTOMER["phone"])
    y -= 30
    box(44, y, W - 88, 30, "JOB / LOCATION", ", ".join(inv["site"][1:]), 11)
    y -= 42
    cols = [(44, 60, "QTY"), (104, 300, "DESCRIPTION"), (404, 80, "PRICE"), (484, W - 44 - 484, "AMOUNT")]
    pg.rect(44, y - 16, W - 88, 16, fill=lighten(col, 0.15), stroke=col)
    for x, w, h in cols:
        pg.t(x + w / 2, y - 11, h, 7.5, "Helvetica-Bold", "c", col)
    y -= 16
    nrows = 12
    rh = 22
    for i in range(nrows):
        pg.line(44, y - rh * (i + 1), W - 44, y - rh * (i + 1), 0.5, col)
    for x, w, h in cols:
        pg.line(x, y, x, y - rh * nrows, 0.6, col)
    pg.line(W - 44, y, W - 44, y - rh * nrows, 0.6, col)
    for i, ln in enumerate(inv["lines"]):
        yy = y - rh * (i + 1) + 6
        hand(74, yy, pg.qty(ln["qty"]), 12, "c")
        hand(110, yy, ln["desc"], 11)
        hand(398 + 80, yy, pg.im(ln["price"]), 12, "r")
        hand(W - 50, yy, pg.im(ln["amount"]), 12, "r")
    y -= rh * nrows
    for r in inv["total_rows"]:
        pg.rect(404, y - 22, W - 448, 22, stroke=col, lw=0.8)
        pg.line(484, y, 484, y - 22, 0.6, col)
        lab = r["label"].upper()
        pg.t(480, y - 14, lab, 7.5, "Helvetica-Bold", "r", col)
        if r["amt"] is not None:
            hand(W - 50, y - 16, pg.m(r["amt"]), 12.5, "r")
        y -= 22
    pg.t(48, 120, "All accounts are due per the terms above. Thank you for your business!", 8, "Helvetica-Oblique", "l", col)
    pg.t(48, 92, "Received by", 7, "Helvetica", "l", col)
    pg.line(100, 92, 330, 92, 0.6, col)


def L_professional(pg):
    s, inv, W, H = pg.sup, pg.inv, pg.W, pg.H
    col = s["color"]
    x0 = 72
    pg.t(x0, H - 72, s["name"], 18, "b", "l", col)
    pg.line(x0, H - 80, W - 72, H - 80, 0.7, col)
    pg.t(x0, H - 93, "   ".join(s["addr"] + supplier_contact(s)), 8, "r", "l", (0.3, 0.3, 0.3))
    y = H - 132
    addr_lines(pg, x0, y, None, [CUSTOMER["legal"], CUSTOMER["attn"]] + CUSTOMER["addr"], 10, 12.5)
    yy = y
    for lab, val in pg.meta(extra=("acct",)):
        pg.t(W - 230, yy, lab + ":", 10, "b", "l")
        pg.t(W - 72, yy, val, 10, "r", "r")
        yy -= 13
    y = min(y - 66, yy) - 20
    pg.t(W / 2, y, "INVOICE", 14, "b", "c", col, cs=4)
    y -= 26
    pg.t(x0, y, "For professional services rendered:", 10, "i", "l")
    y -= 14
    labor = [l for l in inv["lines"] if l["kind"] == "labor"]
    exp = [l for l in inv["lines"] if l["kind"] == "expense"]
    cols = [("Date", 70, "l", lambda l: fmt_date(l["date"], "mdy")), ("Description", 238, "l", lambda l: l["desc"]),
            ("Hours", 50, "r", lambda l: pg.qty(l["qty"])), ("Rate", 50, "r", lambda l: pg.im(l["price"])),
            ("Amount", 60, "r", lambda l: pg.im(l["amount"]))]
    y = items_table(pg, x0, y, cols, labor, 9.5, 18, hw="b", rule_col=(0.75, 0.75, 0.75))
    if exp:
        y -= 14
        pg.t(x0, y, "Expenses advanced:", 10, "i", "l")
        y -= 4
        cols2 = [("", 358, "l", lambda l: l["desc"]), ("", 110, "r", lambda l: pg.im(l["amount"]))]
        y = items_table(pg, x0, y, cols2, exp, 9.5, 18, header=False, rule_col=(0.75, 0.75, 0.75))
    y = totals(pg, W - 170, W - 76, y - 24, 10, 16, line_above_last=True)
    yn = 150
    if s["due_mode"] == "none" and inv["terms_printed"]:
        pass
    pg.t(x0, yn, "Please make checks payable to " + s["name"].split(",")[0] + ". ACH details available on request.",
         9, "r", "l")
    pg.t(x0, yn - 13, "Thank you for the opportunity to be of service.", 9, "i", "l")


def L_plain_template(pg):
    s, inv, W, H = pg.sup, pg.inv, pg.W, pg.H
    col = s["color"]
    pg.t(60, H - 82, "INVOICE", 34, "b", "l", col)
    pg.t(60, H - 110, s["name"], 13, "b", "l")
    addr_lines(pg, 60, H - 125, None, s["addr"] + [s["phone"]], 10, 12.5)
    y = H - 110
    for lab, val in pg.meta(extra=("po",)):
        pg.t(W - 190, y, lab, 10, "b", "l")
        pg.t(W - 60, y, val, 10, "r", "r")
        y -= 14
    y = H - 210
    addr_lines(pg, 60, y, "Bill To:", [CUSTOMER["name"]] + CUSTOMER["addr"], 10, 12.5)
    y -= 70
    cols = [("Description", 280, "l", lambda l: l["desc"]), ("Qty", 60, "c", lambda l: pg.qty(l["qty"])),
            ("Rate", 76, "r", lambda l: pg.im(l["price"])), ("Amount", 76, "r", lambda l: pg.im(l["amount"]))]
    y = items_table(pg, 60, y, cols, inv["lines"], 10, 20, grid=True, hfill=(0.88, 0.88, 0.88),
                    min_rows=len(inv["lines"]) + 2, rule_col=(0.3, 0.3, 0.3))
    totals(pg, W - 150, W - 64, y - 20, 10.5, 17)
    yn = 140
    for n in s["notes"]:
        pg.t(60, yn, n, 10, "i", "l")
        yn -= 13


def L_utility_stub(pg):
    s, inv, W, H = pg.sup, pg.inv, pg.W, pg.H
    col = s["color"]
    grey = (0.93, 0.93, 0.93)
    draw_logo(pg, s["logo"], 36, H - 84, 44, col, initials(s["name"]))
    pg.t(88, H - 58, s["name"], 15, "b", "l", col)
    pg.t(88, H - 71, s["addr"][0] + ", " + s["addr"][1], 8, "r", "l")
    pg.t(88, H - 81, " · ".join(supplier_contact(s)), 8, "r", "l")
    # header block
    meta = [("Account Number", s["acct"])] + pg.meta()
    y = H - 50
    for lab, val in meta:
        pg.t(W - 140, y, lab, 8, "r", "r")
        pg.t(W - 36, y, val, 8.5, "b", "r")
        y -= 11
    # account summary
    y = H - 132
    pg.rect(36, y - 84, 250, 84, fill=grey)
    pg.t(44, y - 14, "ACCOUNT SUMMARY", 8, "b", "l", col)
    rows = [("Previous Balance", inv["prev_bal"]), ("Payment Received - Thank You", -inv["prev_bal"]),
            ("Current Invoice Charges", inv["total"]), ("Total Amount Due", inv["total"])]
    for i, (lab, v) in enumerate(rows):
        b = "b" if i == 3 else "r"
        pg.t(44, y - 30 - i * 13, lab, 8.5, b, "l")
        pg.t(278, y - 30 - i * 13, pg.m(v), 8.5, b, "r")
    pg.line(44, y - 60, 278, y - 60, 0.5)
    addr_lines(pg, 318, y - 14, "SERVICE ADDRESS", ship_to(inv), 8.5, 11, tsize=7.5, tcol=col)
    pg.t(318, y - 66, f"Service period: {pg.d(inv['svc_from'])} - {pg.d(inv['svc_to'])}", 8, "r", "l")
    y -= 104
    pg.t(36, y, "CURRENT CHARGES", 8, "b", "l", col)
    cols = [("Description", 300, "l", lambda l: l["desc"]), ("Qty", 50, "r", lambda l: pg.qty(l["qty"])),
            ("Rate", 80, "r", lambda l: pg.im(l["price"])), ("Amount", 110, "r", lambda l: pg.im(l["amount"]))]
    y = items_table(pg, 36, y - 4, cols, inv["lines"], 8.5, 15, hfill=lighten(col, 0.15), rules=True,
                    rule_col=(0.8, 0.8, 0.8))
    y = totals(pg, W - 150, W - 40, y - 16, 9, 13)
    pg.rect(36, 252, W - 72, 34, stroke=(0.7, 0.7, 0.7))
    for k, n in enumerate(s["notes"][:2]):
        pg.t(44, 274 - k * 11, n, 7.5, "r", "l")
    # tear-off stub
    pg.line(20, 236, W - 20, 236, 0.8, (0.4, 0.4, 0.4), dash=([4, 3], 0))
    pg.t(W / 2, 226, "Please detach and return this portion with your payment", 7, "i", "c", (0.4, 0.4, 0.4))
    pg.t(36, 200, s["name"], 11, "b", "l", col)
    sy = 182
    stub = [("Account Number", s["acct"]), (pg.L["num"], pg.num())]
    if pg.due_text():
        stub.append((pg.L["due"], pg.due_text()))
    stub.append(("Amount Due", pg.m(inv["total"])))
    for lab, val in stub:
        pg.rect(300, sy - 16, 140, 16, stroke=(0.5, 0.5, 0.5))
        pg.rect(440, sy - 16, 136, 16, stroke=(0.5, 0.5, 0.5))
        pg.t(306, sy - 11, lab, 8, "r", "l")
        pg.t(570, sy - 11, val, 8.5, "b", "r")
        sy -= 16
    pg.rect(300, sy - 16, 140, 16, stroke=(0.5, 0.5, 0.5))
    pg.rect(440, sy - 16, 136, 16, stroke=(0.5, 0.5, 0.5))
    pg.t(306, sy - 11, "Amount Enclosed", 8, "r", "l")
    pg.t(450, sy - 11, "$", 8.5, "r", "l")
    addr_lines(pg, 36, 150, None, [CUSTOMER["legal"].upper()] + [a.upper() for a in CUSTOMER["addr"]], 8.5, 11)
    addr_lines(pg, 300, 72, "Remit to:", [s["legal"].upper()] + [a.upper() for a in s["addr"]], 8.5, 11, tsize=7.5)


LAYOUT_FN = {name: globals()["L_" + name] for name in LAYOUTS}


def render_clean(inv, path):
    c = canvas.Canvas(str(path), pagesize=inv["sup"]["page"], invariant=1, pageCompression=1)
    c.setTitle(f"Invoice {inv['id']}")
    c.setAuthor(inv["sup"]["name"])
    c.setCreator("make_set.py")
    pg = Pg(c, inv)
    LAYOUT_FN[inv["sup"]["layout"]](pg)
    c.showPage()
    c.save()
    return pg.drawn


# ----------------------------------------------------------------------------- scan and photo
def rasterize(pdf_path, dpi):
    doc = pymupdf.open(str(pdf_path))
    pix = doc[0].get_pixmap(dpi=dpi, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3).copy()
    doc.close()
    return img


def jpeg_bytes(img, quality):
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "JPEG", quality=int(quality), optimize=False)
    return buf.getvalue()


def image_pdf(jpg, path, page_w, page_h):
    c = canvas.Canvas(str(path), pagesize=(page_w, page_h), invariant=1)
    c.drawImage(ImageReader(io.BytesIO(jpg)), 0, 0, page_w, page_h)
    c.showPage()
    c.save()


def make_scan(clean_pdf, out_pdf, seed):
    r = np.random.default_rng(seed)
    dpi = int(r.choice([150, 200, 200]))
    img = rasterize(clean_pdf, dpi).astype(np.float32)
    h, w = img.shape[:2]
    pw, ph = LETTER
    bed_h, bed_w = int(ph / 72 * dpi), int(pw / 72 * dpi)
    bed = np.full((bed_h, bed_w, 3), 255.0, np.float32)
    if h < bed_h or w < bed_w:  # small ticket laid on the glass
        bed[:] = (238, 238, 236)
        oy, ox = int(r.uniform(0.04, 0.10) * bed_h), int(r.uniform(0.05, 0.15) * bed_w)
        bed[oy:oy + h, ox:ox + w] = img[: bed_h - oy, : bed_w - ox]
        img = bed
    gray = r.random() < 0.7
    if gray:
        g = img @ np.array([0.299, 0.587, 0.114], np.float32)
        img = np.repeat(g[..., None], 3, axis=2)
    else:
        img = img * np.array([1.0, 0.985, 0.95], np.float32)
    # paper/sensor tone and contrast
    img = 255 - (255 - img) * r.uniform(0.82, 0.98)
    img = img * r.uniform(0.92, 0.99)
    ang = r.uniform(-1.8, 1.8)
    M = cv2.getRotationMatrix2D((bed_w / 2, bed_h / 2), ang, 1.0)
    M[:, 2] += r.uniform(-12, 12, 2) * dpi / 200
    fill = float(r.uniform(225, 250))
    img = cv2.warpAffine(img, M, (bed_w, bed_h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                         borderValue=(fill, fill, fill))
    # lid shadow along one edge + uneven lamp
    yy, xx = np.mgrid[0:bed_h, 0:bed_w].astype(np.float32)
    edge = r.integers(0, 4)
    dist = [xx, bed_w - xx, yy, bed_h - yy][edge] / (0.08 * bed_w)
    shade = 1 - r.uniform(0.15, 0.35) * np.exp(-dist)
    shade *= 1 - r.uniform(0.0, 0.08) * (xx / bed_w)
    img = img * shade[..., None]
    # specks and a faint fold line sometimes
    for _ in range(int(r.integers(10, 60))):
        cy, cx = int(r.integers(0, bed_h)), int(r.integers(0, bed_w))
        cv2.circle(img, (cx, cy), int(r.integers(1, 3)), (60, 60, 60), -1)
    if r.random() < 0.4:
        fy = int(bed_h * r.uniform(0.3, 0.7))
        img[fy - 1:fy + 2] *= 0.86
    img = cv2.GaussianBlur(img, (0, 0), r.uniform(0.35, 0.8) * dpi / 200)
    img += r.normal(0, r.uniform(2.5, 6.0), img.shape).astype(np.float32)
    img = np.clip(img, 0, 255).astype(np.uint8)
    image_pdf(jpeg_bytes(img, r.uniform(42, 72)), out_pdf, pw, ph)


def background(r, h, w):
    kind = r.integers(0, 4)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    if kind == 0:  # wood desk
        base = np.array([120, 84, 52], np.float32) * r.uniform(0.8, 1.15)
        grain = np.sin((yy * 0.02 + 6 * np.sin(xx * 0.004 + r.uniform(0, 6))) * r.uniform(1.5, 3)) * 18
        bg = base + grain[..., None] * np.array([1, 0.8, 0.6], np.float32)
    elif kind == 1:  # grey laminate
        bg = np.full((h, w, 3), r.uniform(120, 170), np.float32) * np.array([1, 1, 1.03], np.float32)
    elif kind == 2:  # dark counter
        bg = np.full((h, w, 3), r.uniform(35, 60), np.float32) * np.array([1, 1.02, 1.05], np.float32)
    else:  # stainless steel workbench
        bg = np.full((h, w, 3), r.uniform(150, 185), np.float32)
        bg += (np.sin(xx * 0.9) * 4 + r.normal(0, 4, (h, w)))[..., None]
    bg += r.normal(0, 5, (h, w, 1)).astype(np.float32)
    return bg


def make_photo(clean_pdf, out_pdf, seed):
    r = np.random.default_rng(seed)
    page = rasterize(clean_pdf, 200)
    ph, pw = page.shape[:2]
    # page not flat: gentle bow and curl
    yy, xx = np.mgrid[0:ph, 0:pw].astype(np.float32)
    amp = r.uniform(4, 14)
    mapx = xx + amp * 0.3 * np.sin(yy / ph * math.pi * r.uniform(0.8, 1.6))
    mapy = yy + amp * np.sin(xx / pw * math.pi) * float(r.choice([-1, 1])) + amp * 0.4 * (xx / pw) ** 2
    mapx, mapy = mapx.astype(np.float32), mapy.astype(np.float32)
    page = cv2.remap(page, mapx, mapy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE).astype(np.float32)
    # paper shading from the curl
    page *= (1 - 0.06 * np.abs(np.sin(xx / pw * math.pi * r.uniform(1, 2))))[..., None]
    # output frame (phone portrait 3:4)
    long_side = max(ph, pw)
    OH = int(long_side * r.uniform(1.12, 1.22))
    OW = int(OH * 0.75)
    bg = background(r, OH, OW)
    scale = min(OW * r.uniform(0.80, 0.90) / pw, OH * r.uniform(0.82, 0.92) / ph)
    tw, th = pw * scale, ph * scale
    cx, cy = OW / 2 + r.uniform(-0.03, 0.03) * OW, OH / 2 + r.uniform(-0.03, 0.03) * OH
    ang = math.radians(r.uniform(-5, 5))
    corners = np.array([[-tw / 2, -th / 2], [tw / 2, -th / 2], [tw / 2, th / 2], [-tw / 2, th / 2]], np.float32)
    rot = np.array([[math.cos(ang), -math.sin(ang)], [math.sin(ang), math.cos(ang)]], np.float32)
    dst = corners @ rot.T + np.array([cx, cy], np.float32)
    # perspective: the phone is tilted toward one edge
    k = r.uniform(0.02, 0.06)
    if r.random() < 0.5:
        dst[0, 0] += k * tw; dst[1, 0] -= k * tw   # top narrower
    else:
        dst[1, 1] += k * th * 0.5; dst[2, 1] -= k * th * 0.5  # right side farther
    dst += r.uniform(-0.01, 0.01, dst.shape).astype(np.float32) * tw
    src = np.array([[0, 0], [pw, 0], [pw, ph], [0, ph]], np.float32)
    H_ = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(page, H_, (OW, OH), flags=cv2.INTER_AREA)
    mask = cv2.warpPerspective(np.ones((ph, pw), np.float32), H_, (OW, OH), flags=cv2.INTER_LINEAR)
    # drop shadow under the page
    sh = cv2.GaussianBlur(cv2.warpAffine(mask, np.float32([[1, 0, 10], [0, 1, 14]]), (OW, OH)), (0, 0), 14)
    bg *= (1 - 0.45 * sh)[..., None]
    img = warped * mask[..., None] + bg * (1 - mask[..., None])
    # light: warm cast, gradient from a window, vignette, a soft hand/phone shadow
    Y, X = np.mgrid[0:OH, 0:OW].astype(np.float32)
    a = r.uniform(0, 2 * math.pi)
    grad = 1 - r.uniform(0.10, 0.28) * (0.5 + 0.5 * ((X / OW - 0.5) * math.cos(a) + (Y / OH - 0.5) * math.sin(a)) * 2)
    vig = 1 - r.uniform(0.10, 0.25) * (((X / OW - 0.5) ** 2 + (Y / OH - 0.5) ** 2) * 2.2)
    light = grad * vig
    if r.random() < 0.6:
        sx, sy = r.uniform(0.1, 0.9) * OW, r.choice([r.uniform(0.75, 1.05), r.uniform(-0.05, 0.25)]) * OH
        blob = np.exp(-(((X - sx) / (0.30 * OW)) ** 2 + ((Y - sy) / (0.20 * OH)) ** 2))
        light *= 1 - r.uniform(0.12, 0.28) * blob
    cast = np.array([1.0, r.uniform(0.95, 0.99), r.uniform(0.86, 0.95)], np.float32)
    img = img * light[..., None] * cast
    img = cv2.GaussianBlur(img, (0, 0), r.uniform(0.6, 1.25))
    if r.random() < 0.3:  # slight hand shake
        kl = int(r.integers(3, 6))
        kern = np.zeros((kl, kl), np.float32)
        kern[kl // 2, :] = 1.0 / kl
        kern = cv2.warpAffine(kern, cv2.getRotationMatrix2D((kl / 2 - 0.5, kl / 2 - 0.5), r.uniform(0, 180), 1), (kl, kl))
        if kern.sum() > 0.5:
            kern /= kern.sum()
            img = cv2.filter2D(img, -1, kern)
    img += r.normal(0, r.uniform(3, 7), img.shape).astype(np.float32)
    img = np.clip(img, 0, 255).astype(np.uint8)
    out_w = 1500
    img = cv2.resize(img, (out_w, int(OH * out_w / OW)), interpolation=cv2.INTER_AREA)
    pw_pt = 8.5 * 72 * 1.05
    image_pdf(jpeg_bytes(img, r.uniform(58, 82)), out_pdf, pw_pt, pw_pt * img.shape[0] / img.shape[1])


# ----------------------------------------------------------------------------- self-checks on what was drawn
def check_drawn(inv, drawn):
    lab, sup = inv["label"], inv["sup"]
    blob = "\n".join(drawn)
    pg = Pg.__new__(Pg)
    pg.sup = sup
    must = [lab["supplier"]]
    if lab["invoice_number"]:
        must.append(lab["invoice_number"])
    if lab["invoice_date"]:
        must.append(fmt_date(inv["date"], sup["date_fmt"]))
    if lab["due_source"] == "printed":
        must.append(fmt_date(inv["due_printed"], sup["date_fmt"]))
    if lab["terms"]:
        must.append(lab["terms"])
    for f in ("subtotal", "tax", "total"):
        if lab[f] is not None:
            must.append(fmt_money(D(lab[f]), sup["money"]))
    for m in must:
        assert m in blob, (inv["id"], m)


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    register_fonts()
    invs = build_invoices(args.seed)
    for d in RENDER_DIR.values():
        shutil.rmtree(OUT / d, ignore_errors=True)
        (OUT / d).mkdir()
    tmp = OUT / ".tmp_clean"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    records = []
    for inv in invs:
        r = inv["render"]
        path = OUT / RENDER_DIR[r] / f"{inv['id']}.pdf"
        if r == "clean":
            drawn = render_clean(inv, path)
        else:
            src = tmp / f"{inv['id']}.pdf"
            drawn = render_clean(inv, src)
            (make_scan if r == "scan" else make_photo)(src, path, [args.seed, inv["idx"], 7])
        check_drawn(inv, drawn)
        lab = inv["label"]
        rec = dict(id=inv["id"], file=f"holdout-2/{RENDER_DIR[r]}/{inv['id']}.pdf")
        rec.update({f: lab[f] for f in FIELDS})
        rec.update(exception=lab["exception"], exception_reasons=lab["exception_reasons"], terms=lab["terms"],
                   due_source=lab["due_source"], layout=inv["sup"]["layout"], render=r,
                   sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        records.append(rec)
    shutil.rmtree(tmp)
    out = dict(fields=FIELDS, frozen=None, generator="make_set.py", seed=args.seed,
               layouts=sorted({v["sup"]["layout"] for v in invs}, key=LAYOUTS.index), invoices=records)
    (OUT / "labels.json").write_text(json.dumps(out, indent=2) + "\n")
    # summary
    from collections import Counter
    print("renders:", dict(Counter(r["render"] for r in records)))
    print("layouts:", dict(Counter(r["layout"] for r in records)))
    print("exceptions:", sum(r["exception"] for r in records))
    print("reasons:", dict(Counter(x for r in records for x in r["exception_reasons"])))
    print("injected:", dict(Counter(t for v in invs for t in v["injected"])))


if __name__ == "__main__":
    main()
