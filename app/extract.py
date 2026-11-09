"""Turn an invoice PDF into structured fields.

Two extractors share one output shape:
  * Built-in parser - pdf text layer (pdfplumber) or OCR (tesseract) + label rules.
    Always available, deterministic, no network.
  * Claude - used when ANTHROPIC_API_KEY is set. Reads the PDF directly (text or
    scanned) and returns JSON that matches a fixed schema.

Either way, the extractor only reports what is printed. All business decisions
happen later in rules.py, so the decision logic is the same whichever extractor ran.
"""
from __future__ import annotations

import base64
import json
import os
import re
from datetime import date, datetime

import pdfplumber

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")

# --------------------------------------------------------------------------- reading


def _upright(page):
    """Drop rotated characters (stamps like 'COPY' or 'PAID') so they do not split table rows."""
    def keep(o):
        if o.get("object_type") != "char":
            return True
        m = o.get("matrix") or (1, 0, 0, 1, 0, 0)
        return abs(m[1]) < 0.01 and abs(m[2]) < 0.01

    return page.filter(keep)


def read_document(path: str) -> dict:
    """Return the text of the document and how it was obtained."""
    with pdfplumber.open(path) as pdf:
        pages = len(pdf.pages)
        text = "\n".join((_upright(p).extract_text() or "") for p in pdf.pages)
        stamp = "".join(c["text"] for p in pdf.pages for c in p.chars
                        if abs((c.get("matrix") or (1, 0))[1]) >= 0.01).strip()
    if len(re.sub(r"\s", "", text)) >= 40:
        return {"method": "text", "text": text, "pages": pages, "ocr_quality": None, "stamp": stamp or None}

    # No usable text layer -> scanned image. One OCR pass at a moderate resolution keeps this
    # fast and light enough for a small server (0.1 CPU / 512 MB on Render's free plan).
    import pypdfium2 as pdfium
    import pytesseract

    os.environ.setdefault("OMP_THREAD_LIMIT", "1")  # tesseract threads only fight each other on a tiny CPU
    doc = pdfium.PdfDocument(path)
    chunks, confs = [], []
    for i in range(min(len(doc), 5)):
        img = doc[i].render(scale=2.5).to_pil().convert("L").point(lambda v: 255 if v > 170 else 0)
        data = pytesseract.image_to_data(img, config="--psm 6", output_type=pytesseract.Output.DICT,
                                         timeout=int(os.environ.get("OCR_TIMEOUT", "120")))
        del img
        lines = {}
        for j, word in enumerate(data["text"]):
            if word.strip():
                lines.setdefault((data["block_num"][j], data["par_num"][j], data["line_num"][j]), []).append(word)
                if float(data["conf"][j]) >= 0:
                    confs.append(float(data["conf"][j]))
        chunks.append("\n".join(" ".join(ws) for ws in lines.values()))
    quality = round(sum(confs) / len(confs), 1) if confs else 0.0
    return {"method": "ocr", "text": "\n".join(chunks), "pages": pages, "ocr_quality": quality, "stamp": None}


# --------------------------------------------------------------------------- helpers

AMOUNT_RE = r"\(?-?\s?(?:[$€£]|USD|EUR|GBP)?\s?-?\d[\d.,' ]*\d(?:\s?(?:EUR|USD|GBP|€))?\)?"


def parse_amount(raw: str | None) -> float | None:
    if raw is None:
        return None
    s = raw.strip()
    neg = s.startswith("(") and s.endswith(")") or "-" in s
    s = re.sub(r"[^\d.,]", "", s)
    if not s or not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):  # 1.234,56
            s = s.replace(".", "").replace(",", ".")
        else:  # 1,234.56
            s = s.replace(",", "")
    elif "," in s:
        if re.fullmatch(r"\d{1,3}(,\d{3})+", s):
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    elif s.count(".") > 1:  # OCR: 1.060.00
        head, tail = s.rsplit(".", 1)
        s = head.replace(".", "") + "." + tail
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


MONTHS = "january february march april may june july august september october november december".split()


def parse_date(raw: str | None) -> tuple[str | None, str | None]:
    """Return (iso_date, note). note explains any ambiguity that was resolved."""
    if not raw:
        return None, None
    s = raw.strip().rstrip(".,")
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return _mk(int(m[1]), int(m[2]), int(m[3])), None
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
    if m:
        return _mk(int(m[3]), int(m[2]), int(m[1])), None
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        if a > 12:
            return _mk(y, b, a), None
        note = None if b > 12 or a == b else "Slash date read as month/day (US vendor)"
        return _mk(y, a, b), note
    m = re.match(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", s)
    if m:
        mo = _month(m[1])
        if mo:
            return _mk(int(m[3]), mo, int(m[2])), None
    m = re.match(r"(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})", s)
    if m:
        mo = _month(m[2])
        if mo:
            return _mk(int(m[3]), mo, int(m[1])), None
    return None, None


def _month(name):
    name = name.lower()
    for i, full in enumerate(MONTHS):
        if full.startswith(name[:3]):
            return i + 1
    return None


def _mk(y, m, d):
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


DATE_VALUE = r"(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[./]\d{1,2}[./]\d{4}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4})"


def find(pattern, text, flags=re.I):
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def norm_account(v):
    return re.sub(r"[^A-Z0-9]", "", v.upper()) if v else None


# --------------------------------------------------------------------------- built-in parser


def parse_text(text: str, ocr: bool = False) -> dict:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    notes: list[str] = []

    title_zone = "\n".join(lines[:6])
    is_credit = bool(re.search(r"credit\s*(note|memo)", title_zone, re.I))

    inv_no = find(
        r"(?:credit\s*(?:note|memo)\s*(?:no\.?|number|#)|invoice\s*(?:no\.?|number|#)|inv\.?\s*(?:no\.?|#)|bill\s*(?:no\.?|#)|rechnungsnummer[^:]*)\s*[:#]?\s*([A-Z]{1,5}[-\s]?\d[\w\-/]*)",
        text,
    )
    if inv_no:
        inv_no = re.sub(r"\s", "-", inv_no.upper())

    inv_date_raw = find(r"(?:invoice\s*date|date\s*of\s*issue|datum\s*/\s*date|issue\s*date|(?<![a-z])date)\s*[:.]?\s*" + DATE_VALUE, text)
    due_raw = find(r"(?:due\s*date|payment\s*due|f(?:ae|ä)llig\s*/\s*due|(?<![a-z])due)\s*[:.]?\s*" + DATE_VALUE, text)
    inv_date, n1 = parse_date(inv_date_raw)
    due_date, _ = parse_date(due_raw)
    if n1:
        notes.append(n1)

    po = find(
        r"(?:p\.?\s?o\.?\s*(?:no\.?|number|#|ref)?|purchase\s*order(?:\s*(?:no\.?|number|#))?|order\s*ref(?:erence)?|your\s*order(?:\s*ref)?)\s*[:#.]?\s*(PO[-\s]?\d{3,6})",
        text,
    )
    po_source = "printed"
    if not po:
        po = find(r"\b(PO[-\s]?\d{4})\b", text)
        po_source = "mentioned" if po else None
    if po:
        po = "PO-" + re.sub(r"\D", "", po)

    currency = detect_currency(text)

    # Totals block -------------------------------------------------------
    subtotal = tax = total = None
    tax_rate = None
    for ln in lines:
        low = ln.lower()
        amts = re.findall(AMOUNT_RE, ln)
        if not amts:
            continue
        val = parse_amount(amts[-1])
        if re.match(r"(sub[\s-]?total|net\s*amount|net\s*total|netto)", low):
            subtotal = val
        elif re.match(r"(sales\s*tax|tax|vat|gst|mwst)", low) and "tax id" not in low:
            tax = val
            r = re.search(r"(\d+(?:[.,]\d+)?)\s?%", ln)
            if r:
                tax_rate = float(r.group(1).replace(",", "."))
        elif re.match(r"(total|amount\s*due|balance\s*due|grand\s*total|gesamt)", low):
            total = val
            r = re.search(r"(\d+(?:[.,]\d+)?)\s?%", ln)
            if r and tax_rate is None:
                tax_rate = float(r.group(1).replace(",", "."))
    tax_inclusive = bool(re.search(r"incl(?:\.|uding|usive)?\s*(?:of\s*)?(?:[\d.]+%\s*)?(?:sales\s*)?tax|tax\s*included", text, re.I))
    if is_credit and total is not None and total > 0:
        total = -total

    # Line items ---------------------------------------------------------
    items = []
    for ln in lines:
        it = parse_line_item(ln, allow_repair=ocr)
        if it:
            items.append(it)
            if it.get("repaired"):
                notes.append(f"Unit price on '{it['description']}' re-derived from amount / qty (OCR misread)")

    # Vendor identity ----------------------------------------------------
    vendor_name = lines[0] if lines else None
    if vendor_name:
        vendor_name = re.sub(r"\s+(INVOICE|CREDIT NOTE|Invoice)$", "", vendor_name).strip()
    tax_id = find(r"(?:tax\s*id|ein|vat\s*(?:id|reg\w*)|ust-?idnr\.?)\s*[:#]?\s*([A-Z]{0,2}\s?\d[\d\-]{6,})", text)
    bank_acct = find(r"(?:iban|account\s*(?:no\.?|number|#)|a/c\s*(?:no\.?)?)\s*[:#,]?\s*([A-Z]{0,2}\d[\d ]{5,}\d)", text)
    bank_routing = find(r"(?:routing\s*(?:no\.?|number)?|bic|swift|aba)\s*[:#,]?\s*([A-Z0-9]{6,11})", text)
    bank_note = bool(re.search(r"new\s+(bank|banking|remittance)\s+details|changed?\s+(our\s+)?bank", text, re.I))

    conf = 0.8 if ocr else 0.97
    fields = {
        "document_type": "credit_note" if is_credit else "invoice",
        "vendor_name": vendor_name,
        "vendor_tax_id": tax_id.replace(" ", "") if tax_id else None,
        "invoice_number": inv_no,
        "invoice_date": inv_date,
        "due_date": due_date,
        "po_number": po,
        "po_source": po_source,
        "currency": currency,
        "subtotal": subtotal,
        "tax_amount": tax,
        "tax_rate": tax_rate,
        "tax_inclusive": tax_inclusive,
        "total": total,
        "bank_account": norm_account(bank_acct),
        "bank_routing": norm_account(bank_routing),
        "bank_change_notice": bank_note,
        "line_items": items,
    }
    confidence = {k: (conf if fields[k] not in (None, [], "") else 0.0) for k in (
        "vendor_name", "invoice_number", "invoice_date", "po_number", "total", "bank_account")}
    return {"fields": fields, "confidence": confidence, "notes": notes}


def detect_currency(text):
    counts = {
        "EUR": len(re.findall(r"€|\bEUR\b", text)),
        "GBP": len(re.findall(r"£|\bGBP\b", text)),
        "INR": len(re.findall(r"₹|\bINR\b", text)),
        "USD": len(re.findall(r"\$|\bUSD\b", text)),
    }
    best = max(counts, key=counts.get)
    return best if counts[best] else "USD"


NUM_TOKEN = r"-?\(?[$€£]?\s?-?\d[\d.,]*\)?(?:\s?(?:EUR|USD|€))?"
LINE_RE = re.compile(rf"^(?P<desc>.*?[A-Za-z].*?)\s+(?P<a>{NUM_TOKEN})\s+(?P<b>{NUM_TOKEN})\s+(?P<c>{NUM_TOKEN})$")
SKIP_WORDS = re.compile(r"^(subtotal|total|tax|sales|net|amount|balance|remit|account|routing|bill|invoice|date|due|po\b|iban)", re.I)


def parse_line_item(ln: str, allow_repair=False):
    m = LINE_RE.match(ln)
    if not m or SKIP_WORDS.match(m["desc"]):
        return None
    a, b, c = (parse_amount(m[k]) for k in "abc")
    if a is None or b is None or c is None:
        return None
    desc = m["desc"].strip(" -|")
    for qty, unit in ((a, b), (b, a)):
        if qty and abs(abs(qty * unit) - abs(c)) <= max(0.02, abs(c) * 0.001):
            return {"description": desc, "qty": abs(qty), "unit_price": abs(unit), "amount": c}
    if allow_repair and a and float(a).is_integer() and 0 < a < 100000:
        unit = round(abs(c) / a, 2)
        if abs(unit * a - abs(c)) < 0.011:
            return {"description": desc, "qty": a, "unit_price": unit, "amount": c, "repaired": True}
    return None


# --------------------------------------------------------------------------- Claude extractor

LINE_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "qty": {"type": "number"},
        "unit_price": {"type": "number"},
        "amount": {"type": "number"},
    },
    "required": ["description", "qty", "unit_price", "amount"],
    "additionalProperties": False,
}


def _nullable(t):
    return {"type": [t, "null"]}


SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {"type": "string", "enum": ["invoice", "credit_note"]},
        "vendor_name": _nullable("string"),
        "vendor_tax_id": _nullable("string"),
        "invoice_number": _nullable("string"),
        "invoice_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "due_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "po_number": _nullable("string"),
        "currency": {"type": "string", "description": "ISO 4217 code"},
        "subtotal": _nullable("number"),
        "tax_amount": _nullable("number"),
        "tax_rate": _nullable("number"),
        "tax_inclusive": {"type": "boolean"},
        "total": _nullable("number"),
        "bank_account": _nullable("string"),
        "bank_routing": _nullable("string"),
        "bank_change_notice": {"type": "boolean"},
        "line_items": {"type": "array", "items": LINE_SCHEMA},
    },
    "required": ["document_type", "vendor_name", "vendor_tax_id", "invoice_number", "invoice_date", "due_date",
                 "po_number", "currency", "subtotal", "tax_amount", "tax_rate", "tax_inclusive", "total",
                 "bank_account", "bank_routing", "bank_change_notice", "line_items"],
    "additionalProperties": False,
}

PROMPT = """You are reading a vendor invoice for an accounts payable team. Extract exactly what is printed.

Rules:
- Never guess or compute a value that is not printed. Use null when a field is absent or unreadable.
- vendor_name is the company that issued the invoice, not the customer it is billed to.
- po_number: the customer's purchase order reference if printed, normalised like PO-1234.
- Dates as YYYY-MM-DD. Slash dates from US vendors are month/day.
- Amounts as plain numbers. Credit notes use negative amounts for line amounts and totals.
- tax_inclusive is true only when the invoice says prices include tax.
- bank_change_notice is true when the invoice announces new or changed bank details.
- bank_account: account number or IBAN, digits/letters only. bank_routing: routing number, BIC or SWIFT."""


def extract_with_claude(path: str) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    data = base64.standard_b64encode(open(path, "rb").read()).decode()
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{
            "role": "user",
            "content": [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}},
                {"type": "text", "text": PROMPT},
            ],
        }],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("model declined the request")
    text = next(b.text for b in resp.content if b.type == "text")
    f = json.loads(text)
    if f.get("po_number"):
        digits = re.sub(r"\D", "", f["po_number"])
        f["po_number"] = f"PO-{digits}" if digits else None
    f["po_source"] = "printed" if f.get("po_number") else None
    f["bank_account"] = norm_account(f.get("bank_account"))
    f["bank_routing"] = norm_account(f.get("bank_routing"))
    if f.get("invoice_number"):
        f["invoice_number"] = f["invoice_number"].strip().upper()
    confidence = {k: (0.95 if f.get(k) not in (None, "", []) else 0.0) for k in (
        "vendor_name", "invoice_number", "invoice_date", "po_number", "total", "bank_account")}
    return {"fields": f, "confidence": confidence, "notes": []}


def claude_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and os.environ.get("EXTRACTOR", "auto") != "builtin"
