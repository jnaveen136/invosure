"""Generate the demo invoice PDFs in ./samples.

Four different vendor layouts are used on purpose so the extractor cannot rely on
one fixed template. One invoice is rendered as a scanned image (no text layer).

Run:  python scripts/make_samples.py
"""
import io
import json
import random
from pathlib import Path

from PIL import Image, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "samples"
OUT.mkdir(exist_ok=True)

BUYER = ["Accounts Payable Department", "400 Lakeview Avenue", "Chicago, IL 60611"]
W, H = letter


def usd(v):
    s = f"{abs(v):,.2f}"
    return f"-${s}" if v < 0 else f"${s}"


def num(v):
    s = f"{abs(v):,.2f}"
    return f"-{s}" if v < 0 else s


def eur(v):
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} EUR"


# --------------------------------------------------------------------------- layouts

def layout_a(c, d):
    """Classic: vendor top-left, meta block top-right, ruled table."""
    c.setFont("Helvetica-Bold", 17)
    c.drawString(50, H - 60, d["vendor"])
    c.setFont("Helvetica", 9)
    y = H - 76
    for line in d["vendor_addr"]:
        c.drawString(50, y, line)
        y -= 12
    c.setFont("Helvetica-Bold", 20)
    c.drawRightString(W - 50, H - 60, d.get("title", "INVOICE"))
    c.setFont("Helvetica", 9.5)
    y = H - 82
    for label, value in d["meta"]:
        c.drawRightString(W - 50, y, f"{label}: {value}")
        y -= 13
    c.setFont("Helvetica-Bold", 9)
    c.drawString(50, H - 170, "BILL TO")
    c.setFont("Helvetica", 9.5)
    y = H - 183
    for line in BUYER:
        c.drawString(50, y, line)
        y -= 12
    y = H - 250
    c.setFillColor(colors.HexColor("#EEF0F5"))
    c.rect(45, y - 5, W - 90, 18, fill=1, stroke=0)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(52, y, "Description")
    c.drawRightString(380, y, "Qty")
    c.drawRightString(470, y, "Unit Price")
    c.drawRightString(W - 52, y, "Amount")
    c.setFont("Helvetica", 9.5)
    y -= 22
    for desc, qty, unit, amt in d["lines"]:
        c.drawString(52, y, desc)
        c.drawRightString(380, y, str(qty))
        c.drawRightString(470, y, num(unit))
        c.drawRightString(W - 52, y, num(amt))
        y -= 18
    c.line(45, y + 8, W - 45, y + 8)
    y -= 10
    for label, value in d["totals"]:
        bold = label.lower().startswith("total")
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 10 if bold else 9.5)
        c.drawRightString(470, y, label)
        c.drawRightString(W - 52, y, value)
        y -= 15
    footer(c, d, 120)


def layout_b(c, d):
    """Modern: dark header band, labels like 'Bill #', 'Your order ref'."""
    c.setFillColor(colors.HexColor("#1F2A44"))
    c.rect(0, H - 110, W, 110, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(50, H - 55, d["vendor"])
    c.setFont("Helvetica", 9)
    c.drawString(50, H - 72, "  ".join(d["vendor_addr"]))
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(W - 50, H - 55, d.get("title", "Invoice"))
    c.setFillColor(colors.black)
    y = H - 140
    c.setFont("Helvetica", 9.5)
    for label, value in d["meta"]:
        c.drawString(50, y, f"{label}   {value}")
        y -= 14
    c.setFont("Helvetica-Bold", 9)
    c.drawString(360, H - 140, "Billed to")
    c.setFont("Helvetica", 9.5)
    yy = H - 154
    for line in BUYER:
        c.drawString(360, yy, line)
        yy -= 12
    y -= 25
    c.setFont("Helvetica-Bold", 9)
    c.drawString(50, y, "Item")
    c.drawRightString(390, y, "Qty")
    c.drawRightString(480, y, "Rate")
    c.drawRightString(W - 50, y, "Line total")
    c.line(50, y - 5, W - 50, y - 5)
    c.setFont("Helvetica", 9.5)
    y -= 22
    for desc, qty, unit, amt in d["lines"]:
        c.drawString(50, y, desc)
        c.drawRightString(390, y, str(qty))
        c.drawRightString(480, y, usd(unit))
        c.drawRightString(W - 50, y, usd(amt))
        y -= 18
    y -= 10
    for label, value in d["totals"]:
        c.setFont("Helvetica-Bold" if "due" in label.lower() else "Helvetica", 10)
        c.drawRightString(480, y, label)
        c.drawRightString(W - 50, y, value)
        y -= 15
    footer(c, d, 110)


def layout_c(c, d):
    """European: dd.mm.yyyy dates, 1.234,56 number format, IBAN."""
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, H - 60, d["vendor"])
    c.setFont("Helvetica", 8.5)
    c.drawString(50, H - 74, " | ".join(d["vendor_addr"]))
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, H - 120, "Rechnung / Invoice")
    c.setFont("Helvetica", 9.5)
    y = H - 140
    for label, value in d["meta"]:
        c.drawString(50, y, f"{label}: {value}")
        y -= 13
    yy = H - 120
    for line in BUYER:
        c.drawString(380, yy, line)
        yy -= 12
    y -= 25
    c.setFont("Helvetica-Bold", 9)
    c.drawString(50, y, "Pos. Beschreibung / Description")
    c.drawRightString(370, y, "Menge")
    c.drawRightString(460, y, "Einzelpreis")
    c.drawRightString(W - 50, y, "Gesamt")
    c.setFont("Helvetica", 9.5)
    y -= 20
    for desc, qty, unit, amt in d["lines"]:
        c.drawString(50, y, desc)
        c.drawRightString(370, y, str(qty))
        c.drawRightString(460, y, eur(unit))
        c.drawRightString(W - 50, y, eur(amt))
        y -= 18
    y -= 8
    for label, value in d["totals"]:
        c.setFont("Helvetica-Bold" if "total" in label.lower() else "Helvetica", 10)
        c.drawRightString(460, y, label)
        c.drawRightString(W - 50, y, value)
        y -= 15
    footer(c, d, 120)


def layout_d(c, d):
    """Minimal service-style invoice, slash dates, no table rules."""
    c.setFont("Helvetica-Bold", 15)
    c.drawString(50, H - 60, d["vendor"])
    c.setFont("Helvetica", 9)
    y = H - 75
    for line in d["vendor_addr"]:
        c.drawString(50, y, line)
        y -= 12
    y -= 18
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, y, d.get("title", "Invoice"))
    c.setFont("Helvetica", 9.5)
    y -= 18
    for label, value in d["meta"]:
        c.drawString(50, y, f"{label} {value}")
        y -= 13
    y -= 6
    c.drawString(50, y, "Customer: " + ", ".join(BUYER))
    y -= 32
    c.setFont("Helvetica-Bold", 9)
    c.drawString(50, y, "Service")
    c.drawRightString(380, y, "Units")
    c.drawRightString(470, y, "Price")
    c.drawRightString(W - 50, y, "Total")
    c.setFont("Helvetica", 9.5)
    y -= 20
    for desc, qty, unit, amt in d["lines"]:
        c.drawString(50, y, desc)
        c.drawRightString(380, y, str(qty))
        c.drawRightString(470, y, num(unit))
        c.drawRightString(W - 50, y, num(amt))
        y -= 18
    y -= 12
    for label, value in d["totals"]:
        c.drawRightString(470, y, label)
        c.drawRightString(W - 50, y, value)
        y -= 15
    footer(c, d, 110)


def footer(c, d, y):
    c.setFont("Helvetica", 8.5)
    for line in d.get("notes", []):
        c.drawString(50, y + 30, line)
        y -= 12
    for line in d.get("bank", []):
        c.drawString(50, y, line)
        y -= 11
    if d.get("stamp"):
        c.saveState()
        c.setFillColor(colors.HexColor("#C0392B"))
        c.setFont("Helvetica-Bold", 30)
        c.translate(330, 420)
        c.rotate(18)
        c.drawCentredString(0, 0, d["stamp"])
        c.restoreState()


LAYOUTS = {"A": layout_a, "B": layout_b, "C": layout_c, "D": layout_d}


def us_bank(acct, routing, bank="First Lakes Bank"):
    return [f"Remit to: {bank}   Account No: {acct}   Routing No: {routing}"]


def render(d, path, scanned=False):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setTitle(d.get("title", "Invoice"))
    LAYOUTS[d["layout"]](c, d)
    c.showPage()
    c.save()
    pdf_bytes = buf.getvalue()
    if not scanned:
        path.write_bytes(pdf_bytes)
        return
    import pypdfium2 as pdfium

    page = pdfium.PdfDocument(pdf_bytes)[0]
    img = page.render(scale=2.4).to_pil().convert("L")
    img = img.rotate(0.7, expand=False, fillcolor=255)
    rnd = random.Random(7)
    px = img.load()
    for _ in range(9000):
        x, y = rnd.randrange(img.width), rnd.randrange(img.height)
        px[x, y] = rnd.randrange(150, 230)
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    img.save(path, "PDF", resolution=200)


def us_totals(sub, tax_rate=0.0825, tax_label="Sales Tax (8.25%)", total_label="Total Due"):
    tax = round(sub * tax_rate, 2)
    return [("Subtotal", usd(sub)), (tax_label, usd(tax)), (total_label, usd(round(sub + tax, 2)))]


NW = dict(vendor="Northwind Office Supplies Inc.",
          vendor_addr=["1200 Commerce Way", "Elk Grove, IL 60007", "Tax ID: 84-2219034"],
          bank=us_bank("4410 0098 1123", "021000021"))
BP = dict(vendor="Bluepeak Logistics LLC",
          vendor_addr=["88 Freightline Rd", "Dallas, TX 75207", "EIN 47-1182650"])
SC = dict(vendor="Summit Cloud Services Ltd",
          vendor_addr=["500 Howard St, Suite 900", "San Francisco, CA 94105", "Tax ID: 91-5530217"],
          bank=us_bank("3022 1187 5540", "121000248", "Pacific Union Bank"))
BL = dict(vendor="Brightline Electronics Corp",
          vendor_addr=["7 Circuit Plaza", "Plano, TX 75024", "Tax ID 27-6650431"],
          bank=us_bank("2290 7765 1204", "111000025", "Lone Star National"))
GL = dict(vendor="Greenleaf Facility Services",
          vendor_addr=["3301 W Fulton St", "Chicago, IL 60624", "Tax ID: 36-7712094"],
          bank=us_bank("5500 1982 3377", "071000013"))


def lines(*rows):
    return [(d, q, u, round(q * u, 2)) for d, q, u in rows]


MD = dict(vendor="Meridian Catering Co",
          vendor_addr=["45 Market Hall Lane", "Minneapolis, MN 55401", "Tax ID: 61-4478210"],
          bank=us_bank("1900 3344 6672", "091000019", "North Star Bank"))
OR = dict(vendor="Orion Electrical Contractors",
          vendor_addr=["2100 Peachtree Industrial Blvd", "Atlanta, GA 30341", "Tax ID: 52-3390118"],
          bank=us_bank("6100 4428 1990", "061000052", "Southern Trust Bank"))
BP_BANK = us_bank("7781 2290 4410", "026009593", "Texas Commerce Bank")

G_HAPPY = "Clean invoices (happy path)"
G1 = "1. Split invoices"
G2 = "2. Duplicate invoices"
G3 = "3. Partial delivery"
G4 = "4. Missing or conflicting information"
GS = "Standard checks (simple pass or fail)"
GT = "Same-time processing"
HB = dict(vendor="Harbor Furniture Co",
          vendor_addr=["1800 Lakeshore Industrial Dr", "Cleveland, OH 44114", "Tax ID: 45-6612309"],
          bank=us_bank("3300 1278 4455", "044000037", "Great Lakes Commerce Bank"))

SAMPLES = [
    # ---------------------------------------------------------------- happy path
    dict(file="01_clean_invoice_northwind.pdf", group=G_HAPPY, label="Clean invoice, three lines, everything matches",
         expect="Approved", layout="A", **NW,
         meta=[("Invoice No", "NW-24817"), ("Invoice Date", "2026-10-28"), ("Due Date", "2026-11-27"), ("PO Number", "PO-1001")],
         lines=lines(("A4 copier paper (box of 5 reams)", 40, 42.50), ("Ink cartridges black", 25, 38.00), ("Desk organizers", 30, 12.75)),
         totals=us_totals(3032.50)),

    dict(file="02_clean_supplies_northwind.pdf", group=G_HAPPY, label="Office supplies, delivered in full",
         expect="Approved", layout="A", **NW,
         meta=[("Invoice No", "NW-24880"), ("Invoice Date", "2026-11-04"), ("Due Date", "2026-12-04"), ("PO Number", "PO-1018")],
         lines=lines(("Printer toner black", 12, 64.00), ("Copy paper letter (case)", 15, 38.50)),
         totals=us_totals(1345.50)),
    dict(file="03_clean_freight_bluepeak.pdf", group=G_HAPPY, label="Freight service, different invoice layout",
         expect="Approved", layout="B", **BP, bank=BP_BANK,
         meta=[("Bill #", "BL-77201"), ("Date of issue:", "November 5, 2026"), ("Payment due:", "November 20, 2026"), ("Your order ref:", "PO-1019")],
         lines=[("Freight Chicago to Denver (FTL)", 2, 2150.00, 4300.00)],
         totals=[("Net amount", usd(4300)), ("Tax (exempt)", usd(0)), ("Amount due", usd(4300))]),
    dict(file="04_clean_subscription_summit.pdf", group=G_HAPPY, label="Monthly cloud subscription, two service lines",
         expect="Approved", layout="D", **SC,
         meta=[("Invoice #:", "SC-3417"), ("Date:", "11/05/2026"), ("Due:", "12/05/2026"), ("PO:", "PO-1020")],
         lines=[("Cloud hosting November", 1, 4200.00, 4200.00), ("Support plan Premium November", 1, 900.00, 900.00)],
         totals=[("Subtotal", num(5100)), ("Tax", num(0)), ("Total", usd(5100))]),
    dict(file="05_clean_small_service_greenleaf.pdf", group=G_HAPPY, label="Small one-line service invoice",
         expect="Approved", layout="D", **GL,
         meta=[("Invoice #:", "GL-10412"), ("Date:", "11/04/2026"), ("Due:", "12/04/2026"), ("PO:", "PO-1021")],
         lines=[("Pest control service November", 1, 350.00, 350.00)],
         totals=[("Subtotal", num(350)), ("Tax", num(0)), ("Total", usd(350))]),
    dict(file="06_clean_hardware_brightline.pdf", group=G_HAPPY, label="Hardware with sales tax, goods received",
         expect="Approved", layout="B", **BL,
         meta=[("Bill #", "BLE-5582"), ("Date of issue:", "November 5, 2026"), ("Payment due:", "December 5, 2026"), ("Your order ref:", "PO-1022")],
         lines=[("USB-C hubs", 20, 39.00, 780.00)],
         totals=[("Net amount", usd(780)), ("Sales tax 8.25%", usd(64.35)), ("Amount due", usd(844.35))]),
    dict(file="07_clean_catering_meridian.pdf", group=G_HAPPY, label="Recurring office service",
         expect="Approved", layout="D", **MD,
         meta=[("Invoice #:", "MC-5590"), ("Date:", "11/06/2026"), ("Due:", "11/21/2026"), ("PO:", "PO-1023")],
         lines=[("Office coffee service November", 1, 620.00, 620.00)],
         totals=[("Subtotal", num(620)), ("Sales tax 8.25%", num(51.15)), ("Total", usd(671.15))]),

    # ---------------------------------------------------------------- 1. split invoices
    dict(file="02_split_po_third_invoice_northwind.pdf", group=G1, label="Third invoice on one PO, stays within what is left",
         expect="Approved", layout="A", **NW,
         meta=[("Invoice No", "NW-24790"), ("Invoice Date", "2026-10-25"), ("Due Date", "2026-11-24"), ("PO Number", "PO-1004")],
         lines=lines(("Ergonomic office chairs", 10, 189.00), ("Standing desks", 5, 412.00)),
         totals=us_totals(3950.00)),
    dict(file="03_split_po_overbilled_northwind.pdf", group=G1, label="Another invoice on the same PO bills more chairs than were ordered",
         expect="Rejected", layout="A", **NW,
         meta=[("Invoice No", "NW-24855"), ("Invoice Date", "2026-11-02"), ("Due Date", "2026-12-02"), ("PO Number", "PO-1004")],
         lines=lines(("Ergonomic office chairs", 15, 189.00)),
         totals=us_totals(2835.00)),
    dict(file="04_split_to_dodge_approval_limit_orion.pdf", group=G1, label="Job split into two bills, each just under the approval limit",
         expect="Needs review", layout="A", **OR,
         meta=[("Invoice No", "OE-2208"), ("Invoice Date", "2026-11-04"), ("Due Date", "2026-12-04"), ("PO Number", "PO-1014")],
         lines=lines(("Electrical panel upgrade Building B - labour", 1, 24000.00)),
         totals=[("Subtotal", usd(24000)), ("Sales Tax (exempt)", usd(0)), ("Total Due", usd(24000))]),

    # ---------------------------------------------------------------- 2. duplicates
    dict(file="05_duplicate_already_paid_northwind.pdf", group=G2, label="Already paid in September, re-sent as a reminder copy",
         expect="Rejected", layout="A", **NW, stamp="COPY - REMINDER",
         meta=[("Invoice No", "NW-24533"), ("Invoice Date", "2026-09-30"), ("Due Date", "2026-10-30"), ("PO Number", "PO-0990")],
         lines=lines(("Toner cartridges assorted", 30, 41.50)),
         totals=us_totals(1245.00)),
    dict(file="06_duplicate_number_reformatted_bluepeak.pdf", group=G2, label="Same invoice re-sent with the number written differently",
         expect="Rejected", layout="B", **BP, bank=BP_BANK,
         meta=[("Bill #", "BL77120"), ("Date of issue:", "October 5, 2026"), ("Payment due:", "October 20, 2026"), ("Your order ref:", "PO-0992")],
         lines=[("Freight Chicago to Memphis (LTL)", 2, 1590.00, 3180.00)],
         totals=[("Net amount", usd(3180)), ("Tax (exempt)", usd(0)), ("Amount due", usd(3180))]),
    dict(file="07_possible_duplicate_bluepeak.pdf", group=G2, label="New number, but same amount as a bill from the day before",
         expect="Needs review", layout="B", **BP, bank=BP_BANK,
         meta=[("Bill #", "BL-77158"), ("Date of issue:", "October 29, 2026"), ("Payment due:", "November 13, 2026"), ("Your order ref:", "PO-1002")],
         lines=[("Freight Chicago to Dallas (FTL)", 3, 1850.00, 5550.00)],
         totals=[("Net amount", usd(5550)), ("Tax (exempt)", usd(0)), ("Amount due", usd(5550))]),
    dict(file="08_same_number_other_vendor_meridian.pdf", group=G2, label="Invoice number already used, but by a different vendor",
         expect="Approved", layout="D", **MD,
         meta=[("Invoice #:", "INV-1042"), ("Date:", "11/02/2026"), ("Due:", "11/17/2026"), ("PO:", "PO-1013")],
         lines=[("Team lunch catering Oct 30", 1, 1500.00, 1500.00)],
         totals=[("Subtotal", num(1500)), ("Sales tax 8.25%", num(123.75)), ("Total", usd(1623.75))]),

    # ---------------------------------------------------------------- 3. partial delivery
    dict(file="09_billed_more_than_delivered_brightline.pdf", group=G3, label="Bills all 25 monitors, only 18 have arrived",
         expect="Needs review", layout="B", **BL,
         meta=[("Bill #", "BLE-5560"), ("Date of issue:", "October 31, 2026"), ("Payment due:", "November 30, 2026"), ("Your order ref:", "PO-1006")],
         lines=[("24 inch monitors", 25, 179.00, 4475.00)],
         totals=[("Net amount", usd(4475)), ("Sales tax 8.25%", usd(369.19)), ("Amount due", usd(4844.19))]),
    dict(file="10_partial_delivery_billed_correctly_brightline.pdf", group=G3, label="30 of 40 keyboards delivered, invoice bills exactly 30",
         expect="Approved", layout="B", **BL,
         meta=[("Bill #", "BLE-5571"), ("Date of issue:", "November 2, 2026"), ("Payment due:", "December 2, 2026"), ("Your order ref:", "PO-1016")],
         lines=[("Wireless keyboards", 30, 49.00, 1470.00)],
         totals=[("Net amount", usd(1470)), ("Sales tax 8.25%", usd(121.28)), ("Amount due", usd(1591.28))]),

    # ---------------------------------------------------------------- 4. missing or conflicting info
    dict(file="11_missing_number_and_date_greenleaf.pdf", group=G4, label="Invoice number and date are missing",
         expect="Needs review", layout="D", **GL,
         meta=[("PO:", "PO-1012")],
         lines=[("Office cleaning October", 1, 2400.00, 2400.00), ("Window cleaning", 1, 480.00, 480.00)],
         totals=[("Subtotal", num(2880)), ("Tax", num(0)), ("Total", usd(2880))]),
    dict(file="12_totals_conflict_greenleaf.pdf", group=G4, label="Line items add up to less than the stated total",
         expect="Needs review", layout="A", **GL,
         meta=[("Invoice No", "GL-10388"), ("Invoice Date", "2026-10-31"), ("Due Date", "2026-11-30"), ("PO Number", "PO-1012")],
         lines=lines(("Office cleaning October", 1, 2400.00), ("Window cleaning", 1, 480.00)),
         totals=[("Subtotal", usd(2980)), ("Sales Tax (exempt)", usd(0)), ("Total Due", usd(2980))]),
    dict(file="13_no_po_number_inferred_summit.pdf", group=G4, label="No PO number printed, the right PO is worked out",
         expect="Approved", layout="D", **SC,
         meta=[("Invoice #:", "SC-3402"), ("Date:", "11/03/2026"), ("Due:", "12/03/2026")],
         lines=[("Data backup service October", 1, 1250.00, 1250.00)],
         totals=[("Subtotal", num(1250)), ("Tax", num(0)), ("Total", usd(1250))]),
    dict(file="14_wrong_po_number_printed_northwind.pdf", group=G4, label="Printed PO belongs to another vendor, lines match a different PO",
         expect="Needs review", layout="A", **NW,
         meta=[("Invoice No", "NW-24870"), ("Invoice Date", "2026-11-03"), ("Due Date", "2026-12-03"), ("PO Number", "PO-1012")],
         lines=lines(("Whiteboard markers (pack)", 50, 9.80), ("Sticky notes (12 pack)", 40, 14.25)),
         totals=us_totals(1060.00)),
    dict(file="15_scanned_invoice_northwind.pdf", group=G4, label="Scanned paper invoice, read with OCR",
         expect="Approved", layout="A", scanned=True, **NW,
         meta=[("Invoice No", "NW-24861"), ("Invoice Date", "2026-11-03"), ("Due Date", "2026-12-03"), ("PO Number", "PO-1017")],
         lines=lines(("A3 printer paper (ream)", 20, 31.00), ("Binder clips (box)", 30, 4.50)),
         totals=us_totals(755.00)),

    # ---------------------------------------------------------------- same-time processing
    dict(file="22_same_time_a_harbor.pdf", group=GT, label="$15,000 against a PO with $20,000 left (run with the next one)",
         expect="Approved", layout="A", **HB,
         meta=[("Invoice No", "HF-1101"), ("Invoice Date", "2026-11-06"), ("Due Date", "2026-12-06"), ("PO Number", "PO-1026")],
         lines=lines(("Ergonomic desk chairs", 30, 500.00)),
         totals=[("Subtotal", usd(15000)), ("Sales Tax (exempt)", usd(0)), ("Total Due", usd(15000))]),
    dict(file="23_same_time_b_harbor.pdf", group=GT, label="Another $15,000 on the same PO, arriving at the same moment",
         expect="Rejected", layout="A", **HB,
         meta=[("Invoice No", "HF-1102"), ("Invoice Date", "2026-11-07"), ("Due Date", "2026-12-07"), ("PO Number", "PO-1026")],
         lines=lines(("Ergonomic desk chairs", 30, 500.00)),
         totals=[("Subtotal", usd(15000)), ("Sales Tax (exempt)", usd(0)), ("Total Due", usd(15000))]),

    # ---------------------------------------------------------------- standard checks
    dict(file="16_bank_account_changed_bluepeak.pdf", group=GS, label="Bank account on the invoice differs from our records",
         expect="Rejected", layout="B", **BP,
         meta=[("Bill #", "BL-77195"), ("Date of issue:", "November 1, 2026"), ("Payment due:", "November 16, 2026"), ("Your order ref:", "PO-1009")],
         lines=[("Warehouse storage October", 1, 3400.00, 3400.00)],
         totals=[("Net amount", usd(3400)), ("Tax (exempt)", usd(0)), ("Amount due", usd(3400))],
         notes=["IMPORTANT: Please note our new banking details, effective immediately."],
         bank=us_bank("5512 0098 7766", "322271627", "Coastal Federal Credit Union")),
    dict(file="17_unknown_vendor_apex.pdf", group=GS, label="Vendor is not in the approved vendor list",
         expect="Rejected", layout="A", vendor="Apex Tech Traders",
         vendor_addr=["Unit 14, 220 Industrial Pkwy", "Newark, NJ 07114", "Tax ID: 22-9081734"],
         bank=us_bank("9900 1234 5678", "021200339", "Garden State Bank"),
         meta=[("Invoice No", "AT-0091"), ("Invoice Date", "2026-11-01"), ("Due Date", "2026-11-15"), ("PO Number", "PO-7731")],
         lines=lines(("USB-C cables 2m", 100, 6.50)),
         totals=us_totals(650.00)),
    dict(file="18_price_above_po_summit.pdf", group=GS, label="Price is 8.8% above the PO, over the tolerance",
         expect="Rejected", layout="D", **SC,
         meta=[("Invoice #:", "SC-3391"), ("Date:", "11/02/2026"), ("Due:", "12/02/2026"), ("PO:", "PO-1003")],
         lines=[("Cloud hosting October", 1, 4650.00, 4650.00), ("Support plan Premium", 1, 900.00, 900.00)],
         totals=[("Subtotal", num(5550)), ("Tax", num(0)), ("Total", usd(5550))]),
]


def main():
    for old in OUT.glob("*.pdf"):  # the folder is fully regenerated
        old.unlink()
    index = []
    import re
    for i, s in enumerate(SAMPLES, 1):  # file numbers follow the list order
        s["file"] = f"{i:02d}_" + re.sub(r"^\d+_", "", s["file"])
        render(s, OUT / s["file"], scanned=s.get("scanned", False))
        index.append({"file": s["file"], "group": s["group"], "label": s["label"], "expect": s["expect"]})
    (OUT / "index.json").write_text(json.dumps(index, indent=2))
    print(f"wrote {len(index)} samples to {OUT}")


if __name__ == "__main__":
    main()
