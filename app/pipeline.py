"""The invoice pipeline.

Every invoice goes through the same stages. Four of them are the key checks that
are always shown, with evidence and uncertainty:

    1. Split invoices         - billing across several invoices on one PO, and
                                bills split to stay under the approval limit
    2. Duplicate invoices     - same file, same number (also reformatted),
                                or same amount within a few days
    3. Partial delivery       - billed only for what has actually been received
    4. Missing or conflicting - missing fields, totals that disagree, a PO that is
       information              missing or contradicts the invoice, scan quality

All other checks are "standard checks": simple pass or fail. A failed standard
check rejects the invoice, except the approval limit, which asks for sign-off.

Outcomes:
    Approved      every check passed
    Rejected      a confirmed violation of a business rule
    Needs review  missing information, ambiguity or an unresolved discrepancy
"""
from __future__ import annotations

import hashlib
import re
import time
import traceback
from datetime import date, timedelta

from rapidfuzz import fuzz, process

from . import extract, store

STAGES = [
    ("read", "Read document"),
    ("extract", "Extract invoice fields"),
    ("info", "Missing or conflicting information"),
    ("standard", "Standard checks"),
    ("duplicates", "Duplicate invoices"),
    ("split", "Split invoices"),
    ("delivery", "Partial delivery"),
    ("decide", "Decision"),
]
# The four key checks, in the order they are presented.
KEY_CHECKS = [("split", "Split invoices"), ("duplicates", "Duplicate invoices"),
              ("delivery", "Partial delivery"), ("info", "Missing or conflicting information")]

APPROVED, REVIEW, REJECTED = "Approved", "Needs review", "Rejected"

ROUTES = {
    "vendor": "Vendor",
    "ap": "AP clerk",
    "ap_lead": "AP lead",
    "procurement": "Procurement",
    "controller": "Finance controller",
    "manager": "Finance manager",
    "receiving": "Receiving team",
}


def money(v, cur="USD"):
    if v is None:
        return "-"
    sym = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹"}.get(cur, cur + " ")
    s = f"{abs(v):,.2f}"
    return f"-{sym}{s}" if v < 0 else f"{sym}{s}"


def nice_date(iso):
    if not iso:
        return "-"
    d = date.fromisoformat(iso)
    return d.strftime("%b %d, %Y").replace(" 0", " ")


def norm_number(n):
    """Invoice numbers are compared without punctuation or spacing: BL-77120 == BL77120 == bl 77120."""
    return re.sub(r"[^A-Z0-9]", "", (n or "").upper())


def chk(label, status, detail=""):
    return {"label": label, "status": status, "detail": detail}


class Ctx:
    def __init__(self, run):
        self.run = run
        self.path = run["file_path"]
        self.filename = run["filename"]
        self.file_hash = run.get("file_hash")
        self.ref = store.reference()
        self.rules = store.load_rules()
        self.today = store.today()
        self.issues: list[dict] = []
        self.f: dict = {}
        self.vendor = None
        self.po = None
        self.net = None
        self.matched: list[dict] = []
        self.uncertain: list[str] = []
        self.low_confidence = False
        self.extractor = "Built-in parser"
        self.history = [r for r in store.list_runs() if r["id"] != run["id"] and r["status"] == "done"]

    def issue(self, code, severity, route, message, source, ask_vendor=None):
        self.issues.append({"code": code, "severity": severity, "route": route, "message": message,
                            "source": source, "ask_vendor": ask_vendor})

    def cur(self):
        return self.f.get("currency") or "USD"

    def is_credit(self):
        return self.f.get("document_type") == "credit_note"


class StopPipeline(Exception):
    def __init__(self, stage_result):
        self.stage_result = stage_result


def key_result(outcome, verdict, evidence, uncertain, checks, data=None):
    """Result shape for the four key checks."""
    status = {"pass": "pass", "review": "warn", "reject": "fail", "na": "skip"}[outcome]
    return {"status": status, "outcome": outcome, "summary": verdict, "verdict": verdict,
            "evidence": evidence, "uncertain": uncertain, "checks": checks, "data": data or {}}


# =========================================================================== read + extract


def s_read(c: Ctx):
    raw = open(c.path, "rb").read()
    checks = [chk("File received", "pass", f"{c.filename} ({len(raw) / 1024:.0f} KB)")]
    if not raw.startswith(b"%PDF"):
        checks.append(chk("File is a PDF", "fail", "The file is not a PDF"))
        c.issue("not_pdf", "reject", "ap", "The file is not a PDF, so it cannot be processed as an invoice.", "Standard checks",
                ask_vendor="the invoice as a PDF")
        raise StopPipeline({"summary": "Not a PDF", "checks": checks})
    try:
        doc = extract.read_document(c.path)
    except Exception as e:
        checks.append(chk("PDF can be opened", "fail", f"{e.__class__.__name__}"))
        c.issue("unreadable", "reject", "ap", "The PDF is damaged or protected and cannot be read.", "Standard checks",
                ask_vendor="a readable PDF copy of the invoice")
        raise StopPipeline({"summary": "Could not open PDF", "checks": checks})
    c.doc = doc
    if doc["method"] == "text":
        checks.append(chk("Text layer found", "pass", f"{doc['pages']} page(s), digital PDF"))
        summary = "Digital PDF"
    else:
        checks.append(chk("Scanned image, read with OCR", "pass", f"Read confidence {doc['ocr_quality']:.0f}%"))
        summary = f"Scanned, OCR confidence {doc['ocr_quality']:.0f}%"
    if doc.get("stamp"):
        checks.append(chk("Stamp on the page", "warn", f"\"{doc['stamp']}\", ignored while reading"))
    return {"summary": summary, "checks": checks, "data": {"method": doc["method"], "text": doc["text"][:4000]}}


def s_extract(c: Ctx):
    checks, result = [], None
    if extract.claude_available():
        try:
            result = extract.extract_with_claude(c.path)
            c.extractor = f"Claude ({extract.CLAUDE_MODEL})"
        except Exception as e:
            checks.append(chk("Claude extraction", "warn", f"Unavailable ({e.__class__.__name__}); used the built-in parser"))
    if result is None:
        result = extract.parse_text(c.doc["text"], ocr=c.doc["method"] == "ocr")
    c.f = f = result["fields"]
    c.extract_notes = result["notes"]
    labels = [("vendor_name", "Vendor"), ("invoice_number", "Invoice number"), ("invoice_date", "Invoice date"),
              ("po_number", "PO reference"), ("total", "Total")]
    for key, label in labels:
        v = f.get(key)
        shown = money(v, c.cur()) if key == "total" and v is not None else v
        checks.append(chk(label, "pass" if v not in (None, "") else "warn", shown if v not in (None, "") else "Not found"))
    checks.append(chk("Line items", "pass" if f["line_items"] else "warn", f"{len(f['line_items'])} line(s)"))
    found = sum(1 for k, _ in labels if f.get(k) not in (None, ""))
    return {"summary": f"{found} of {len(labels)} key fields found, {c.extractor}", "checks": checks,
            "data": {"fields": f, "extractor": c.extractor}}


# =========================================================================== 4. missing or conflicting


def resolve_vendor(c: Ctx):
    """Find the vendor by tax ID and by name. Returns (vendor, how, conflict_message)."""
    f, vendors = c.f, c.ref["vendors"]
    by_tax = by_name = None
    name_score = 0
    tid = (f.get("vendor_tax_id") or "").replace("-", "").replace(" ", "").upper()
    if tid:
        by_tax = next((v for v in vendors.values() if v["tax_id"].replace("-", "").upper() == tid), None)
    if f.get("vendor_name"):
        names = {n: v for v in vendors.values() for n in [v["name"], *v["aliases"].split("|")]}
        best = process.extractOne(f["vendor_name"], list(names), scorer=fuzz.token_sort_ratio)
        if best and best[1] >= c.rules["vendor_match_threshold"]:
            by_name, name_score = names[best[0]], best[1]
    if by_tax and by_name and by_tax["vendor_id"] != by_name["vendor_id"]:
        return by_tax, "tax ID", (f"The name on the invoice looks like {by_name['name']}, but the tax ID {f['vendor_tax_id']} "
                                  f"belongs to {by_tax['name']}.")
    if by_name and tid and not by_tax:
        return by_name, f"name ({name_score:.0f}% match)", (f"The tax ID on the invoice ({f['vendor_tax_id']}) does not match "
                                                           f"the one on file for {by_name['name']} ({by_name['tax_id']}).")
    if by_tax:
        return by_tax, "tax ID", None
    if by_name:
        return by_name, f"name ({name_score:.0f}% match)", None
    return None, None, None


def lines_fit(c: Ctx, po):
    """How well the invoice lines fit a PO: average best description similarity (0-100)."""
    if not c.f["line_items"]:
        return 0
    sims = [max(fuzz.token_set_ratio(i["description"], l["description"]) for l in po["lines"]) for i in c.f["line_items"]]
    return sum(sims) / len(sims)


def infer_po(c: Ctx):
    """Pick the vendor's open PO that fits the invoice lines and amount. Returns (po, reason) or (None, reason)."""
    if not c.vendor:
        return None, "the vendor is unknown"
    scored = []
    for po in c.ref["pos"].values():
        if po["vendor_id"] != c.vendor["vendor_id"] or po["status"] != "open":
            continue
        fit = lines_fit(c, po)
        billed = billed_so_far(c, po["po_number"])
        remaining = sum((l["qty"] - billed.get(l["line_no"], 0)) * l["unit_price"] for l in po["lines"])
        amount_ok = c.net is not None and remaining > 0 and c.net <= remaining * 1.02
        scored.append((fit + (25 if amount_ok else 0), fit, amount_ok, po))
    if not scored:
        return None, f"{c.vendor['name']} has no open purchase orders"
    scored.sort(key=lambda x: -x[0])
    top = scored[0]
    clear_winner = len(scored) == 1 or top[0] - scored[1][0] >= 20
    if top[1] >= 85 and top[2] and clear_winner:
        others = len(scored) - 1
        return top[3], (f"{top[3]['po_number']} is the only open PO from this vendor whose lines match ({top[1]:.0f}% description match) "
                        f"and which has enough left to cover {money(c.net, c.cur())}" + (f"; {others} other open PO(s) were ruled out" if others else ""))
    return None, "no single open PO matches the lines and amount clearly enough"


def s_info(c: Ctx):
    f, cur = c.f, c.cur()
    checks, evidence, uncertain = [], [], []
    review = []

    # Required fields ----------------------------------------------------------
    missing = [lbl for k, lbl in (("invoice_number", "invoice number"), ("invoice_date", "invoice date"),
                                  ("total", "total amount"), ("vendor_name", "vendor name")) if f.get(k) in (None, "")]
    if missing:
        checks.append(chk("Required fields present", "fail", "Missing: " + ", ".join(missing)))
        review.append(f"The invoice has no {' and no '.join(missing)}.")
        c.issue("missing_fields", "review", "vendor", f"The invoice is missing its {' and '.join(missing)}.", "Missing or conflicting information",
                ask_vendor="a corrected invoice that shows the " + ", ".join(missing))
    else:
        checks.append(chk("Required fields present", "pass", "Vendor, invoice number, date and total"))
        evidence.append(f"Invoice {f['invoice_number']} dated {nice_date(f['invoice_date'])} for {money(f['total'], cur)}.")

    # Totals agree with each other ---------------------------------------------
    lines_sum = round(sum(i["amount"] for i in f["line_items"]), 2) if f["line_items"] else None
    sub, tax, total = f.get("subtotal"), f.get("tax_amount"), f.get("total")
    conflicts = []
    if f.get("tax_inclusive"):
        if tax is None and total is not None and f.get("tax_rate"):
            tax = round(total - total / (1 + f["tax_rate"] / 100), 2)
        c.net = round(total - (tax or 0), 2) if total is not None else lines_sum
        evidence.append(f"Prices include tax, so the amount compared to the PO is {money(c.net, cur)} (total minus {money(tax, cur)} tax).")
        if lines_sum is not None and total is not None and abs(lines_sum - total) > 0.02:
            conflicts.append(f"line items add up to {money(lines_sum, cur)} but the total says {money(total, cur)}")
    else:
        if sub is None:
            sub = lines_sum if lines_sum is not None else (round(total - (tax or 0), 2) if total is not None else None)
        c.net = sub
        if lines_sum is not None and sub is not None and abs(lines_sum - sub) > 0.02:
            conflicts.append(f"line items add up to {money(lines_sum, cur)} but the subtotal says {money(sub, cur)}")
            c.net = lines_sum  # never pay more than what is itemised
        if sub is not None and total is not None and abs(round(sub + (tax or 0), 2) - total) > 0.02:
            conflicts.append(f"subtotal {money(sub, cur)} plus tax {money(tax or 0, cur)} does not equal the total {money(total, cur)}")
    if conflicts:
        checks.append(chk("Amounts agree with each other", "fail", "; ".join(conflicts)))
        review.append("The amounts on the invoice contradict each other: " + "; ".join(conflicts) + ".")
        c.issue("totals_conflict", "review", "vendor", "The amounts on the invoice contradict each other: " + "; ".join(conflicts) + ".",
                "Missing or conflicting information", ask_vendor="a corrected invoice where the line items, subtotal, tax and total agree")
    elif total is not None:
        checks.append(chk("Amounts agree with each other", "pass",
                          f"Lines {money(lines_sum, cur)}" + (f" + tax {money(tax, cur)}" if tax and not f.get('tax_inclusive') else "") + f" = {money(total, cur)}"))
        evidence.append("Line items, subtotal, tax and total agree with each other.")
    if not f["line_items"]:
        uncertain.append("No itemised lines were found, so matching relies on the invoice totals.")

    # Vendor identity ------------------------------------------------------------
    vendor, how, conflict = resolve_vendor(c)
    c.vendor = vendor
    if conflict:
        checks.append(chk("Vendor identity is consistent", "fail", conflict))
        review.append(conflict)
        c.issue("vendor_conflict", "review", "controller", conflict + " Confirm who issued this invoice before paying.",
                "Missing or conflicting information")
    elif vendor:
        checks.append(chk("Vendor identity is consistent", "pass", f"{vendor['name']}, matched by {how}"))
        evidence.append(f"Issuer identified as {vendor['name']} ({vendor['vendor_id']}) by {how}.")

    # Purchase order reference ---------------------------------------------------
    pos = c.ref["pos"]
    printed = f.get("po_number")
    po = pos.get(printed) if printed else None
    if vendor is None:
        c.po = po if po else None
        checks.append(chk("PO reference", "skip", "Cannot be confirmed without a known vendor"))
    elif po and po["vendor_id"] == vendor["vendor_id"]:
        c.po = po
        fit = lines_fit(c, po)
        if f["line_items"] and fit < 60:
            alt, why = infer_po(c)
            msg = f"{printed} is printed, but the invoice lines do not match it ({fit:.0f}% match)" + (f"; they match {alt['po_number']} instead." if alt else ".")
            checks.append(chk("PO reference matches the invoice", "fail", msg))
            review.append(msg)
            c.issue("po_conflict", "review", "procurement", msg + " Confirm the correct PO with the buyer.", "Missing or conflicting information",
                    ask_vendor="confirmation of the purchase order this invoice relates to")
        else:
            checks.append(chk("PO reference matches the invoice", "pass", f"{printed} is printed and belongs to {vendor['name']}"))
            evidence.append(f"{printed} is printed on the invoice and was issued to {vendor['name']} on {nice_date(po['po_date'])}.")
    else:
        alt, why = infer_po(c)
        if printed and po:
            owner = c.ref["vendors"][po["vendor_id"]]["name"]
            msg = f"The invoice says {printed}, but that PO was issued to {owner.rstrip('.')}, not {vendor['name'].rstrip('.')}."
        elif printed:
            msg = f"The invoice says {printed}, but no such PO exists."
        else:
            msg = None
        if msg:
            if alt:
                msg += f" The lines match {alt['po_number']} exactly, so it is most likely a typo."
                c.po = alt
                uncertain.append(f"Checks below were run against {alt['po_number']}, the PO the lines match, not the printed {printed}.")
            checks.append(chk("PO reference matches the invoice", "fail", msg))
            review.append(msg)
            c.issue("po_conflict", "review", "procurement", msg + " Buyer to confirm the correct PO before it is matched.",
                    "Missing or conflicting information", ask_vendor="confirmation of the purchase order number this invoice relates to")
        elif alt:
            c.po = alt
            f["po_number"] = alt["po_number"]
            checks.append(chk("PO reference", "warn", f"Not printed. Matched to {alt['po_number']}"))
            evidence.append(f"No PO number was printed. {why}.")
            uncertain.append(f"The PO was inferred, not printed. The match to {alt['po_number']} is unambiguous, but the vendor did not state it.")
        else:
            checks.append(chk("PO reference", "fail", f"Not printed, and {why}"))
            review.append(f"There is no PO number, and {why}.")
            c.issue("no_po", "review", "procurement", f"No PO number on the invoice, and {why}.", "Missing or conflicting information",
                    ask_vendor="the purchase order number this invoice relates to")

    # Dates -----------------------------------------------------------------------
    if f.get("due_date") and f.get("invoice_date") and f["due_date"] < f["invoice_date"]:
        checks.append(chk("Dates are consistent", "warn", "Due date is before the invoice date"))
        uncertain.append("The due date is before the invoice date; the vendor's payment terms were used instead.")

    # Reading quality -------------------------------------------------------------
    if c.doc["method"] == "ocr":
        q = c.doc["ocr_quality"] or 0
        if q < c.rules["ocr_min_quality"]:
            checks.append(chk("Scan is readable", "fail", f"OCR confidence {q:.0f}%, below {c.rules['ocr_min_quality']:.0f}%"))
            review.append(f"The scan is hard to read ({q:.0f}% confidence).")
            c.low_confidence = True
            c.issue("ocr_quality", "review", "ap", f"The scan is hard to read ({q:.0f}% confidence). A person should confirm the figures.",
                    "Missing or conflicting information")
        else:
            checks.append(chk("Scan is readable", "pass", f"OCR confidence {q:.0f}%"))
            uncertain.append(f"Values were read from a scanned image ({q:.0f}% OCR confidence), not from digital text.")
    for n in c.extract_notes:
        uncertain.append(n + ".")

    c.uncertain += uncertain
    if review:
        verdict = review[0] if len(review) == 1 else f"{len(review)} gaps or contradictions need to be resolved."
        return key_result("review", verdict, evidence, uncertain, checks)
    verdict = "All required information is present and consistent." if not uncertain else \
        "All required information is present and consistent, with the uncertainties listed."
    return key_result("pass", verdict, evidence, uncertain, checks)


# =========================================================================== standard checks


def match_lines(c: Ctx):
    """Map each invoice line to a PO line."""
    po = c.po
    inv_lines = c.f["line_items"] or [{"description": "Invoice total", "qty": 1, "unit_price": c.net or 0, "amount": c.net or 0}]
    gross = sum(abs(i["amount"]) for i in inv_lines) or 1
    factor = (abs(c.net) / gross) if (c.f.get("tax_inclusive") and c.net) else 1.0
    billed = billed_so_far(c, po["po_number"])
    rows, unmatched = [], []
    for il in inv_lines:
        if len(po["lines"]) == 1 and len(inv_lines) == 1:
            pl = po["lines"][0]
        else:
            choices = {l["description"]: l for l in po["lines"]}
            best = process.extractOne(il["description"], list(choices), scorer=fuzz.token_set_ratio)
            pl = choices[best[0]] if best and best[1] >= 60 else None
        if not pl:
            unmatched.append(il["description"])
            continue
        qty = abs(il["qty"])
        net_amt = abs(il["amount"]) * factor
        rows.append({"line_no": pl["line_no"], "description": pl["description"], "qty": qty, "po_qty": pl["qty"],
                     "billed_before": billed.get(pl["line_no"], 0), "po_unit": pl["unit_price"],
                     "inv_unit": round(net_amt / qty, 2) if qty else 0, "net_amount": round(net_amt, 2),
                     "diff": round(net_amt - qty * pl["unit_price"], 2), "line_type": pl["line_type"]})
    return rows, unmatched


def s_standard(c: Ctx):
    f, cur, checks = c.f, c.cur(), []
    S = "Standard checks"

    def fail(label, detail, code, route, message, ask=None, severity="reject"):
        checks.append(chk(label, "fail" if severity == "reject" else "warn", detail))
        c.issue(code, severity, route, message, f"{S}: {label}", ask_vendor=ask)

    v = c.vendor
    # 1-2 vendor
    if not v:
        fail("Vendor is approved", f"\"{f.get('vendor_name')}\" is not in the vendor master", "unknown_vendor", "procurement",
             f"{f.get('vendor_name') or 'The issuer'} is not an approved vendor. Procurement must onboard the vendor before any payment.")
    else:
        checks.append(chk("Vendor is approved", "pass", v["name"]))
        if v["status"] != "active":
            fail("Vendor is active", f"Status is {v['status']}", "blocked_vendor", "procurement",
                 f"{v['name']} is {v['status']} in the vendor master. Invoices from {v['status']} vendors are not paid.")
        else:
            checks.append(chk("Vendor is active", "pass", "Active"))
    # 3 bank account
    acct = f.get("bank_account")
    if v and acct:
        on_file = extract.norm_account(v["bank_account"])
        if acct == on_file:
            checks.append(chk("Bank account matches records", "pass", f"Account ending {acct[-4:]}"))
        else:
            note = " The invoice also announces new bank details." if f.get("bank_change_notice") else ""
            fail("Bank account matches records", f"Invoice: ending {acct[-4:]}. On file: ending {on_file[-4:]}", "bank_changed", "controller",
                 f"The invoice asks for payment to an account ending {acct[-4:]}, but the account on file ends {on_file[-4:]}.{note} "
                 f"Bank changes are only accepted through the verified vendor-update process, confirmed by calling {v['phone']} (the number on file).")
    elif v:
        checks.append(chk("Bank account matches records", "pass", "Not printed; payment goes to the account on file"))
    else:
        checks.append(chk("Bank account matches records", "skip", "No known vendor to compare with"))
    # 4-7 PO based
    po = c.po
    if po:
        if po["status"] != "open":
            fail("PO is open", f"{po['po_number']} is {po['status']}", "po_closed", "procurement",
                 f"{po['po_number']} is {po['status']}; nothing more can be billed against it.")
        else:
            checks.append(chk("PO is open", "pass", po["po_number"]))
        if cur != po["currency"]:
            fail("Currency matches PO", f"Invoice {cur}, PO {po['currency']}", "currency", "procurement",
                 f"The invoice is in {cur} but {po['po_number']} was agreed in {po['currency']}.",
                 ask=f"a re-issued invoice in {po['currency']} as agreed on {po['po_number']}")
        else:
            checks.append(chk("Currency matches PO", "pass", cur))
        rows, unmatched = match_lines(c)
        c.matched = rows
        if unmatched:
            fail("Every line is on the PO", ", ".join(unmatched), "line_not_on_po", "procurement",
                 f"Billed items not on {po['po_number']}: {', '.join(unmatched)}.")
        else:
            checks.append(chk("Every line is on the PO", "pass", f"{len(rows)} line(s) matched"))
        if rows and not c.is_credit():
            base = sum(r["qty"] * r["po_unit"] for r in rows)
            var = sum(r["diff"] for r in rows)
            tol = min(c.rules["price_tolerance_pct"] / 100 * base, c.rules["price_tolerance_abs"])
            if var > tol + 0.005:
                worst = max(rows, key=lambda r: r["diff"])
                fail("Prices match the PO", f"{money(var, cur)} above PO ({var / base * 100:.1f}%), tolerance {money(tol, cur)}",
                     "price_variance", "procurement",
                     f"Billed {money(var, cur)} ({var / base * 100:.1f}%) above the PO price, more than the {money(tol, cur)} tolerance. "
                     f"Largest difference: {worst['description']} at {money(worst['inv_unit'], cur)} vs {money(worst['po_unit'], cur)} on the PO.",
                     ask="a corrected invoice at the prices agreed on the purchase order")
            else:
                detail = "Exact match" if abs(var) < 0.01 else f"{money(var, cur)} difference, within the {money(tol, cur)} tolerance"
                checks.append(chk("Prices match the PO", "pass", detail))
    else:
        for lbl in ("PO is open", "Currency matches PO", "Every line is on the PO", "Prices match the PO"):
            checks.append(chk(lbl, "skip", "No confirmed PO"))
    # 8 tax
    if f.get("tax_rate") and c.net and f.get("tax_amount") is not None and not f.get("tax_inclusive"):
        exp = round(c.net * f["tax_rate"] / 100, 2)
        if abs(exp - f["tax_amount"]) > 0.05:
            fail("Tax is calculated correctly", f"Expected {money(exp, cur)}, billed {money(f['tax_amount'], cur)}", "tax_wrong", "vendor",
                 f"Tax at {f['tax_rate']:g}% should be {money(exp, cur)}, but {money(f['tax_amount'], cur)} was billed.",
                 ask="a corrected invoice with the right tax amount")
        else:
            checks.append(chk("Tax is calculated correctly", "pass", f"{f['tax_rate']:g}% = {money(exp, cur)}"))
    else:
        checks.append(chk("Tax is calculated correctly", "pass", "No separate tax rate to verify" if not f.get("tax_amount") else "Tax included in prices"))
    # 9 dates
    if f.get("invoice_date"):
        d = date.fromisoformat(f["invoice_date"])
        age = (c.today - d).days
        if d > c.today:
            fail("Invoice date is valid", f"{nice_date(f['invoice_date'])} is in the future", "future_date", "vendor",
                 f"The invoice is dated in the future ({nice_date(f['invoice_date'])}).", ask="an invoice with the correct date")
        elif age > c.rules["max_invoice_age_days"]:
            fail("Invoice date is valid", f"{age} days old, limit {c.rules['max_invoice_age_days']}", "stale", "ap",
                 f"The invoice is {age} days old, past the {c.rules['max_invoice_age_days']}-day limit.")
        else:
            checks.append(chk("Invoice date is valid", "pass", f"{age} days old"))
    else:
        checks.append(chk("Invoice date is valid", "skip", "No date printed"))
    # 10 approval limit (sign-off, not a violation)
    total = f.get("total")
    if total is not None and not c.is_credit() and total >= c.rules["approval_limit"]:
        fail("Within approval limit", f"{money(total, cur)} at or above {money(c.rules['approval_limit'])}", "approval_limit", "manager",
             f"{money(total, cur)} is at or above the {money(c.rules['approval_limit'])} approval limit; a finance manager must sign off.",
             severity="review")
    else:
        checks.append(chk("Within approval limit", "pass", f"Limit {money(c.rules['approval_limit'])}"))

    ran = [x for x in checks if x["status"] != "skip"]
    failed = [x for x in ran if x["status"] in ("fail", "warn")]
    if failed:
        summary = f"{len(failed)} of {len(ran)} failed: " + ", ".join(x["label"].lower() for x in failed)
    else:
        summary = f"All {len(ran)} standard checks passed"
    status = "fail" if any(x["status"] == "fail" for x in ran) else ("warn" if failed else "pass")
    return {"status": status, "summary": summary, "checks": checks,
            "data": {"passed": len(ran) - len(failed), "ran": len(ran), "failed": failed}}


# =========================================================================== 2. duplicates


def prior_invoices(c: Ctx):
    """All earlier invoices: from the ledger and from runs here that were not rejected."""
    out = {}
    for l in c.ref["ledger"]:
        key = (l["vendor_id"], l["invoice_number"])
        p = out.setdefault(key, {"vendor_id": l["vendor_id"], "number": l["invoice_number"], "date": l["invoice_date"],
                                 "amount": 0.0, "status": l["status"], "po": l["po_number"], "source": "ledger"})
        p["amount"] += l["amount"]
    for r in c.history:
        if r["final_status"] == REJECTED or not r.get("vendor_id"):
            continue
        key = (r["vendor_id"], r["invoice_number"] or r["id"])
        out.setdefault(key, {"vendor_id": r["vendor_id"], "number": r["invoice_number"], "date": r["invoice_date"],
                             "amount": r["total"] or 0, "status": r["final_status"].lower(), "po": r["po_number"],
                             "source": "processed here", "hash": r["file_hash"]})
    return list(out.values())


def s_duplicates(c: Ctx):
    f, cur = c.f, c.cur()
    checks, evidence, uncertain = [], [], []
    vid = c.vendor["vendor_id"] if c.vendor else None
    vname = c.vendor["name"] if c.vendor else (f.get("vendor_name") or "this vendor")
    num = f.get("invoice_number")
    nnum = norm_number(num)
    priors = prior_invoices(c)
    mine = [p for p in priors if vid and p["vendor_id"] == vid]
    evidence.append(f"Compared with {len(mine)} earlier invoice{'' if len(mine) == 1 else 's'} from {vname} in the ledger and in this tool."
                    if mine else f"No earlier invoices from {vname} in the ledger or in this tool.")

    # Same file
    same_file = next((r for r in c.history if r["file_hash"] == c.file_hash and r["final_status"] != REJECTED), None)
    if same_file:
        msg = f"This exact file was already processed on {same_file['created_at'][:10]} ({same_file['final_status'].lower()})."
        checks.append(chk("Same file seen before", "fail", msg))
        evidence.append(f"The file fingerprint matches the earlier upload of {same_file['filename']}.")
        c.issue("dup_file", "reject", "ap", msg + " Paying it again would be a duplicate payment.", "Duplicate invoices")
        return key_result("reject", msg, evidence, uncertain, checks)
    checks.append(chk("Same file seen before", "pass", "No"))

    # Same number, same vendor (after normalising)
    if nnum:
        hit = next((p for p in mine if norm_number(p["number"]) == nnum), None)
        if hit:
            same_text = hit["number"].upper() == num.upper()
            msg = (f"Invoice {num} was already {hit['status']} on {nice_date(hit['date'])} for {money(hit['amount'])}."
                   if same_text else
                   f"{num} is the same invoice as {hit['number']} ({hit['status']} on {nice_date(hit['date'])}, {money(hit['amount'])}), written differently.")
            checks.append(chk("Invoice number already used by this vendor", "fail", msg))
            evidence.append(f"{'Ledger' if hit['source'] == 'ledger' else 'Run history'}: {hit['number']}, {nice_date(hit['date'])}, "
                            f"{money(hit['amount'])}, {hit['status']}.")
            if not same_text:
                evidence.append(f"Numbers are compared without dashes or spaces: {num} and {hit['number']} both read {nnum}.")
            if c.doc.get("stamp"):
                evidence.append(f"The page is stamped \"{c.doc['stamp']}\", which is typical of a re-sent copy.")
            if hit.get("date") == f.get("invoice_date"):
                evidence.append("The invoice date is also identical.")
            c.issue("dup_number", "reject", "ap", msg + " Paying it again would be a duplicate payment.", "Duplicate invoices")
            return key_result("reject", msg, evidence, uncertain, checks)
        checks.append(chk("Invoice number already used by this vendor", "pass", f"{num} is new for {vname}"))
        others = [p for p in priors if norm_number(p["number"]) == nnum and p["vendor_id"] != vid]
        for o in others:
            oname = c.ref["vendors"].get(o["vendor_id"], {}).get("name", o["vendor_id"])
            evidence.append(f"{o['number']} also exists for {oname} ({o['status']} {nice_date(o['date'])}). Numbers are only unique per vendor, "
                            "so this is not a duplicate.")
            checks.append(chk("Same number from another vendor", "pass", f"{oname}: not a duplicate"))
    else:
        checks.append(chk("Invoice number already used by this vendor", "skip", "No invoice number to compare"))
        uncertain.append("Without an invoice number, duplicates can only be found by amount and date.")

    # Same amount, close dates, different number
    window = c.rules["near_duplicate_days"]
    amounts = {round(x, 2) for x in (f.get("total"), c.net) if x is not None}
    d0 = date.fromisoformat(f["invoice_date"]) if f.get("invoice_date") else None
    near = []
    for p in mine:
        if norm_number(p["number"]) == nnum or round(p["amount"], 2) not in amounts or not p.get("date"):
            continue
        gap = abs((date.fromisoformat(p["date"]) - d0).days) if d0 else 0
        if d0 is None or gap <= window:
            near.append((p, gap))
    if near:
        p, gap = near[0]
        msg = (f"Same vendor and same amount ({money(p['amount'])}) as {p['number']}, dated {gap} day{'s' if gap != 1 else ''} apart. "
               "It may be the same bill re-issued with a new number, or a genuine repeat order.")
        checks.append(chk("Same amount within a few days", "warn", f"{p['number']}, {nice_date(p['date'])}, {money(p['amount'])}"))
        evidence.append(f"{p['number']} ({p['status']}) was billed on {nice_date(p['date'])} for {money(p['amount'])}" +
                        (f" against {p['po']}" if p.get("po") else "") + ".")
        uncertain.append("A matching amount alone does not prove a duplicate. A person needs to compare both invoices or ask the vendor.")
        c.issue("near_dup", "review", "ap_lead", msg, "Duplicate invoices")
        return key_result("review", f"Possible duplicate of {p['number']}: same amount, {gap} day{'s' if gap != 1 else ''} apart.", evidence, uncertain, checks)
    checks.append(chk("Same amount within a few days", "pass", f"None within {window} days"))
    return key_result("pass", "No duplicate found: new number, new file, and no same-amount invoice nearby.", evidence, uncertain, checks)


# =========================================================================== 1. split invoices


def billed_so_far(c, po_number):
    """Quantity already billed per PO line, from the ledger and from approved runs."""
    billed = {}
    for l in c.ref["ledger"]:
        if l["po_number"] == po_number:
            billed[l["po_line"]] = billed.get(l["po_line"], 0) + l["qty"]
    for r in c.history:
        if r["final_status"] == APPROVED and r["po_number"] == po_number and r.get("result"):
            sign = -1 if r.get("doc_type") == "credit_note" else 1
            for ml in r["result"].get("matched_lines", []):
                billed[ml["line_no"]] = billed.get(ml["line_no"], 0) + sign * ml["qty"]
    return billed


def po_invoice_history(c: Ctx, po_number):
    hist = {}
    for l in c.ref["ledger"]:
        if l["po_number"] == po_number:
            h = hist.setdefault(l["invoice_number"], {"number": l["invoice_number"], "date": l["invoice_date"], "amount": 0.0, "status": l["status"]})
            h["amount"] += l["amount"]
    for r in c.history:
        if r["final_status"] == APPROVED and r["po_number"] == po_number:
            hist.setdefault(r["invoice_number"] or r["id"], {"number": r["invoice_number"] or "(no number)", "date": r["invoice_date"],
                                                            "amount": sum(m["qty"] * m.get("po_unit", 0) for m in (r.get("result") or {}).get("matched_lines", [])) or (r["total"] or 0),
                                                            "status": "approved here"})
    return sorted(hist.values(), key=lambda h: h["date"] or "")


def s_split(c: Ctx):
    f, cur = c.f, c.cur()
    checks, evidence, uncertain = [], [], []
    po = c.po
    if not po or not c.matched:
        return key_result("na", "Not checked: no confirmed purchase order to compare against.", evidence,
                          ["Split billing could not be checked because the PO is not confirmed."], [chk("PO billing history", "skip", "No PO")])
    earlier = po_invoice_history(c, po["po_number"])
    n = len(earlier) + 1
    if earlier:
        evidence.append(f"Earlier invoices on {po['po_number']}: " + "; ".join(
            f"{h['number']} ({nice_date(h['date'])}, {money(h['amount'])}, {h['status']})" for h in earlier) + ".")
    else:
        evidence.append(f"This is the first invoice against {po['po_number']}.")
    over = []
    if c.is_credit():
        for r in c.matched:
            ok = r["qty"] <= r["billed_before"] + 1e-9
            checks.append(chk(f"Credit for {r['description']}", "pass" if ok else "fail", f"Crediting {r['qty']:g} of {r['billed_before']:g} billed"))
            if not ok:
                over.append(r)
    else:
        for r in c.matched:
            total_billed = r["billed_before"] + r["qty"]
            left = r["po_qty"] - r["billed_before"]
            if total_billed > r["po_qty"] + 1e-9:
                over.append(r)
                checks.append(chk(r["description"], "fail",
                                  f"Ordered {r['po_qty']:g}, already billed {r['billed_before']:g}, this invoice {r['qty']:g}: {total_billed - r['po_qty']:g} too many"))
                evidence.append(f"{r['description']}: {r['po_qty']:g} ordered, {r['billed_before']:g} billed on earlier invoices, "
                                f"so only {max(left, 0):g} could still be billed. This invoice bills {r['qty']:g}.")
            else:
                checks.append(chk(r["description"], "pass", f"Ordered {r['po_qty']:g}, billed {total_billed:g} in total including this invoice"))
    # PO usage for the meter
    billed = billed_so_far(c, po["po_number"])
    before = sum(billed.get(l["line_no"], 0) * l["unit_price"] for l in po["lines"])
    this = sum(r["qty"] * r["po_unit"] for r in c.matched) * (-1 if c.is_credit() else 1)
    usage = {"po_total": po["total"], "billed_before": round(before, 2), "this_invoice": round(this, 2),
             "after": round(before + this, 2), "pct_after": round((before + this) / po["total"] * 100, 1) if po["total"] else 0}

    if over:
        r = over[0]
        msg = (f"{r['description']}: {r['po_qty']:g} ordered and {r['billed_before']:g} already billed on earlier invoices, "
               f"but this invoice bills {r['qty']:g} more. That is {r['billed_before'] + r['qty'] - r['po_qty']:g} over the PO.")
        c.issue("over_billed", "reject", "procurement", msg, "Split invoices",
                ask_vendor=f"a corrected invoice; {po['po_number']} has only {max(r['po_qty'] - r['billed_before'], 0):g} "
                           f"{r['description'].lower()} left to bill")
        return key_result("reject", f"Invoice {n} on {po['po_number']} bills more than the PO has left.", evidence, uncertain, checks, {"usage": usage})

    # Bills split to stay under the approval limit
    limit = c.rules["approval_limit"]
    total = f.get("total") or 0
    if not c.is_credit() and 0 < total < limit and c.vendor and f.get("invoice_date"):
        d0 = date.fromisoformat(f["invoice_date"])
        window = c.rules["near_duplicate_days"]
        nnum = norm_number(f.get("invoice_number"))
        recent = [p for p in prior_invoices(c) if p["vendor_id"] == c.vendor["vendor_id"] and p.get("date")
                  and norm_number(p["number"]) != nnum and abs((date.fromisoformat(p["date"]) - d0).days) <= window
                  and p["amount"] < limit]
        combined = total + sum(p["amount"] for p in recent)
        if recent and combined >= limit:
            names = ", ".join(f"{p['number']} ({nice_date(p['date'])}, {money(p['amount'])})" for p in recent)
            msg = (f"On its own {money(total, cur)} is under the {money(limit)} approval limit, but together with {names} "
                   f"{c.vendor['name']} has billed {money(combined)} within {window} days. The work may have been split to avoid sign-off.")
            checks.append(chk("Bills split under the approval limit", "warn", f"Combined {money(combined)} within {window} days"))
            evidence.append(f"Recent invoices from {c.vendor['name']}: {names}. Each is under the limit; together they are {money(combined)}.")
            uncertain.append("Splitting can be legitimate (milestone billing). A finance manager decides; the system cannot tell intent.")
            c.issue("split_limit", "review", "manager", msg, "Split invoices")
            return key_result("review", f"Combined with {len(recent)} recent bill(s), {money(combined)} is over the approval limit.",
                              evidence, uncertain, checks, {"usage": usage})
        checks.append(chk("Bills split under the approval limit", "pass",
                          f"{money(combined)} billed by this vendor within {window} days, under {money(limit)}"))

    left_after = usage["po_total"] - usage["after"]
    evidence.append(f"After this invoice {usage['pct_after']:g}% of {po['po_number']} is billed; {money(max(left_after, 0))} remains.")
    verdict = (f"Invoice {n} on {po['po_number']}. Every line stays within what the PO has left."
               if earlier else f"First invoice on {po['po_number']}, within the ordered quantities.")
    return key_result("pass", verdict, evidence, uncertain, checks, {"usage": usage})


# =========================================================================== 3. partial delivery


def s_delivery(c: Ctx):
    checks, evidence, uncertain = [], [], []
    po = c.po
    if not po or not c.matched:
        return key_result("na", "Not checked: no confirmed purchase order lines.", evidence,
                          ["Deliveries could not be checked because the PO is not confirmed."], [chk("Goods receipts", "skip", "No PO")])
    if c.is_credit():
        return key_result("na", "Not needed for a credit note.", evidence, uncertain, [chk("Goods receipts", "skip", "Credit note")])
    goods = [r for r in c.matched if r["line_type"] == "goods"]
    if not goods:
        evidence.append("Every billed line is a service, so there is nothing physical to receive.")
        return key_result("na", "Services only, so no delivery to confirm.", evidence, uncertain,
                          [chk(r["description"], "skip", "Service") for r in c.matched])
    short, any_partial = [], False
    for r in goods:
        grns = [g for g in c.ref["receipts"] if g["po_number"] == po["po_number"] and g["line_no"] == r["line_no"]]
        recv = sum(g["qty_received"] for g in grns)
        billed_total = min(r["billed_before"] + r["qty"], r["po_qty"])  # over-billing beyond the PO is judged by split invoices
        grn_txt = ", ".join(f"{g['grn_number']} ({nice_date(g['received_date'])}, {g['qty_received']:g})" for g in grns) or "no goods receipt"
        if billed_total > recv + 1e-9:
            gap = billed_total - recv
            short.append((r, recv, billed_total, gap))
            checks.append(chk(r["description"], "fail", f"Received {recv:g} of {r['po_qty']:g}; billed {billed_total:g} in total"))
            evidence.append(f"{r['description']}: received {recv:g} ({grn_txt}); billed {billed_total:g} including this invoice; {gap:g} not delivered yet.")
        else:
            part = recv < r["po_qty"]
            any_partial = any_partial or part
            checks.append(chk(r["description"], "pass", f"Received {recv:g} of {r['po_qty']:g}; billed {billed_total:g} in total"))
            evidence.append(f"{r['description']}: received {recv:g} of {r['po_qty']:g} ({grn_txt}); billed {billed_total:g} in total." +
                            (f" {r['po_qty'] - recv:g} still to be delivered and billed later." if part else ""))
    for r in c.matched:
        if r["line_type"] != "goods":
            checks.append(chk(r["description"], "skip", "Service, no receipt needed"))
    if short:
        r, recv, billed_total, gap = short[0]
        msg = (f"{r['description']}: {recv:g} received but {billed_total:g} billed. {gap:g} have not arrived. "
               "Pay once the warehouse confirms delivery, or pay for the delivered part only.")
        uncertain.append("The rest may be in transit. Receiving should confirm before this is paid.")
        c.issue("not_received", "review", "receiving", msg, "Partial delivery")
        return key_result("review", f"{gap:g} of the billed {r['description'].lower()} have not been delivered yet.", evidence, uncertain, checks)
    verdict = ("Only part of the order has arrived, and the invoice bills exactly what was delivered."
               if any_partial else "Everything billed has been delivered.")
    return key_result("pass", verdict, evidence, uncertain, checks)


# =========================================================================== decision


def s_decide(c: Ctx, stages):
    f, cur = c.f, c.cur()
    is_credit = c.is_credit()
    rank = {"Duplicate invoices": 0, "Split invoices": 1, "Partial delivery": 2, "Missing or conflicting information": 3}
    ordered = sorted(c.issues, key=lambda i: rank.get(i["source"], 4))
    rejects = [i for i in ordered if i["severity"] == "reject"]
    reviews = [i for i in ordered if i["severity"] == "review"]
    status = REJECTED if rejects else REVIEW if reviews else APPROVED
    blocking = rejects or reviews
    vname = c.vendor["name"] if c.vendor else (f.get("vendor_name") or "the vendor")

    # Key checks summary, in presentation order
    by_id = {s["id"]: s for s in stages}
    key = []
    for sid, title in KEY_CHECKS:
        s = by_id.get(sid, {})
        key.append({"id": sid, "title": title, "outcome": s.get("outcome", "na"), "verdict": s.get("verdict", "Not run")})
    std = by_id.get("standard", {}).get("data", {})

    # Why
    if status == APPROVED:
        due = f.get("due_date")
        if not due and f.get("invoice_date") and c.vendor:
            due = (date.fromisoformat(f["invoice_date"]) + timedelta(days=int(c.vendor["payment_terms_days"]))).isoformat()
            c.uncertain.append(f"No due date printed; used {vname}'s {c.vendor['payment_terms_days']}-day terms.")
        why = (f"All four key checks and all {std.get('ran', 0)} standard checks passed. " +
               (f"The credit of {money(abs(f.get('total') or 0), cur)} is valid against {f.get('po_number')}." if is_credit else
                f"The invoice matches {f.get('po_number')} and can be paid."))
        if is_credit:
            next_steps = [f"Apply the {money(abs(f.get('total') or 0), cur)} credit to the next payment to {vname}."]
        elif due and due < c.today.isoformat():
            next_steps = [f"Post to the ledger against {f.get('po_number')}.", f"Already past due ({nice_date(due)}): add {money(f.get('total'), cur)} to the next payment run."]
        else:
            next_steps = [f"Post to the ledger against {f.get('po_number')}.", f"Pay {money(f.get('total'), cur)} on {nice_date(due)} to the bank account on file."]
    else:
        lead = blocking[0]
        why = lead["message"] if len(blocking) == 1 else (
            f"{len(blocking)} {'violations' if status == REJECTED else 'open questions'} found. The main one: {lead['message']}")
        if status == REJECTED:
            next_steps = ["Do not pay this invoice."]
            codes = {i["code"] for i in rejects}
            if codes & {"dup_number", "dup_file"}:
                next_steps.append(f"Tell {vname} this invoice was already received and paid.")
            if "unknown_vendor" in codes:
                next_steps.append("If the purchase was genuine, Procurement onboards the vendor and raises a PO, then the vendor re-sends the invoice.")
            if "bank_changed" in codes:
                next_steps.append(f"Finance controller calls {vname} on the number on file to verify the request. Flag as possible payment fraud.")
            if codes & {"over_billed", "price_variance", "currency", "line_not_on_po", "tax_wrong", "future_date"}:
                next_steps.append(f"Send the drafted email asking {vname} for a corrected invoice.")
            if codes & {"po_closed", "blocked_vendor", "stale", "not_pdf", "unreadable"}:
                next_steps.append("Return the invoice to the sender with the reason above.")
        else:
            buyer = c.po["buyer"] if c.po else "the buyer"
            actions = {
                "vendor": f"Send the drafted email to {vname}; keep the invoice open until the reply arrives.",
                "procurement": f"Procurement ({buyer}) confirms which PO this belongs to.",
                "receiving": "Receiving confirms whether the missing items have arrived, then re-run this invoice.",
                "controller": f"Finance controller confirms who issued this invoice by calling the number on file.",
                "manager": "Finance manager reviews the related bills together and signs off or rejects.",
                "ap_lead": "AP lead compares this invoice with the earlier one and confirms with the vendor whether it is a repeat.",
                "ap": "AP clerk checks the figures against the document.",
            }
            next_steps = []
            for i in reviews:
                if actions[i["route"]] not in next_steps:
                    next_steps.append(actions[i["route"]])

    # Evidence and uncertainty
    evidence = []
    for sid, title in KEY_CHECKS:
        for e in by_id.get(sid, {}).get("evidence", []):
            if e not in evidence:
                evidence.append(e)
    if std:
        failed = std.get("failed", [])
        evidence.append(f"Standard checks: {std['passed']} of {std['ran']} passed" +
                        (". Failed: " + "; ".join(f"{x['label']} ({x['detail']})" for x in failed) if failed else "."))
    uncertain = []
    for u in c.uncertain:
        if u not in uncertain:
            uncertain.append(u)
    for sid in ("duplicates", "split", "delivery"):
        for u in by_id.get(sid, {}).get("uncertain", []):
            if u not in uncertain:
                uncertain.append(u)
    if c.low_confidence or any(i["code"] in ("vendor_conflict", "po_conflict", "totals_conflict") for i in c.issues):
        confidence = "Low"
    elif uncertain:
        confidence = "Medium"
    else:
        confidence = "High"

    # Draft to vendor
    asks = []
    for i in blocking:
        if i.get("ask_vendor") and i["ask_vendor"] not in asks:
            asks.append(i["ask_vendor"])
    vendor_message = None
    if asks and status != APPROVED and c.vendor:
        num = f.get("invoice_number")
        ref = f"invoice {num}" if num else "your invoice" + (f" for {f['po_number']}" if f.get("po_number") else "")
        word = "can't accept" if status == REJECTED else "can't process"
        vendor_message = {
            "to": c.vendor["contact_email"],
            "subject": (f"Invoice {num}" if num else "Your invoice") + (" returned" if status == REJECTED else " on hold") + " - action needed",
            "body": f"Hello {vname} team,\n\nWe received {ref}" + (f" for {money(f.get('total'), cur)}" if f.get("total") is not None else "")
                    + f". We {word} it yet because: {blocking[0]['message']}\n\nPlease send:\n" + "\n".join(f"  - {a}" for a in asks)
                    + "\n\nOnce we have this, we will process it right away.\n\nThank you,\nAccounts Payable Team",
        }
    return {
        "status": status,
        "headline": why,
        "why": why,
        "reasons": [{"message": i["message"], "route": ROUTES[i["route"]], "severity": i["severity"], "source": i["source"]} for i in blocking],
        "key_checks": key,
        "standard": {"passed": std.get("passed", 0), "ran": std.get("ran", 0), "failed": std.get("failed", [])},
        "evidence": evidence,
        "uncertain": uncertain,
        "confidence": confidence,
        "route": ", ".join(dict.fromkeys(ROUTES[i["route"]] for i in blocking)) if blocking else ("AP clerk" if is_credit else "Payment run"),
        "next_steps": next_steps,
        "vendor_message": vendor_message,
    }


STAGE_FN = {"read": s_read, "extract": s_extract, "info": s_info, "standard": s_standard,
            "duplicates": s_duplicates, "split": s_split, "delivery": s_delivery}


def run_pipeline(run: dict, delay: float = 0.45):
    """Execute every stage, saving progress after each so the UI can stream it."""
    c = Ctx(run)
    run["stages"] = [{"id": sid, "name": name, "status": "pending", "key": sid in dict(KEY_CHECKS)} for sid, name in STAGES]
    stopped = False
    for idx, (sid, name) in enumerate(STAGES):
        st = run["stages"][idx]
        if sid == "decide":
            break
        if stopped:
            st.update(status="skip", summary="Not run", checks=[], outcome="na", verdict="Not run: the document could not be read.",
                      evidence=[], uncertain=[])
            continue
        st["status"] = "running"
        t0 = time.time()
        store.save_run(run)
        time.sleep(delay)
        try:
            res = STAGE_FN[sid](c)
        except StopPipeline as sp:
            res = {**sp.stage_result, "status": "fail"}
            stopped = True
        except Exception as e:
            traceback.print_exc()
            res = {"status": "fail", "summary": "Unexpected error", "checks": [chk("Unexpected error", "fail", f"{e.__class__.__name__}: {e}")]}
            c.issue("stage_error", "review", "ap", f"The '{name}' step failed unexpectedly, so a person should review this invoice.", name)
        if "status" not in res:
            res["status"] = "pass"
        st.update(res)
        st["ms"] = int((time.time() - t0) * 1000)
        if sid == "extract":
            f = c.f
            run.update(vendor_name=f.get("vendor_name"), invoice_number=f.get("invoice_number"), invoice_date=f.get("invoice_date"),
                       total=f.get("total"), currency=f.get("currency"), doc_type=f.get("document_type"),
                       extractor=c.extractor, po_number=f.get("po_number"))
        if sid == "info":
            if c.vendor:
                run.update(vendor_id=c.vendor["vendor_id"], vendor_name=c.vendor["name"])
            if c.po:
                run["po_number"] = c.po["po_number"]
        store.save_run(run)
    st = run["stages"][-1]
    st["status"] = "running"
    store.save_run(run)
    time.sleep(delay)
    decision = s_decide(c, run["stages"])
    st.update(status={APPROVED: "pass", REVIEW: "warn", REJECTED: "fail"}[decision["status"]],
              summary=decision["status"], checks=[], data=decision)
    run.update(decision=decision["status"], final_status=decision["status"], route=decision["route"], status="done")
    run["result"] = {"matched_lines": [{"line_no": r["line_no"], "qty": r["qty"], "po_unit": r["po_unit"]} for r in c.matched],
                     "issues": c.issues}
    store.save_run(run)
    return run
