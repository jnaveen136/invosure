# InvoSure

Takes a vendor invoice PDF and returns a reasoned decision: **Approved**, **Needs review** or **Rejected**. It shows every step as it runs, says who has to act next, and drafts the email to the vendor when something is missing.

Built for the AI Solutions Analyst case study, PS-1 (Finance / AP).

## Run it locally

```bash
pip install -r requirements.txt
sudo apt-get install -y tesseract-ocr     # macOS: brew install tesseract
uvicorn app.main:app --port 8000
```

Open http://localhost:8000. The 18 test invoices are already in `samples/`. To regenerate them, run `python scripts/make_samples.py`.

Optional: set `ANTHROPIC_API_KEY` to have Claude read the invoices instead of the built-in parser. The decision rules are the same either way. If Claude fails, the run falls back to the built-in parser and says so in the "Extract" step.

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | unset | Turns on Claude extraction |
| `CLAUDE_MODEL` | `claude-opus-5-5` | Model used for extraction |
| `EXTRACTOR` | `auto` | Set to `builtin` to force the local parser |
| `STAGE_DELAY` | `0.45` | Seconds between steps so the live view is easy to follow |
| `TODAY` | system date | Pin "today" (YYYY-MM-DD) for repeatable demos |

## Deploy (public link)

Any host that runs a Dockerfile works. For example, on Render:

1. Push this folder to a GitHub repo.
2. On render.com, choose New, then Web Service, and pick the repo. Render detects the Dockerfile.
3. Optionally add `ANTHROPIC_API_KEY` under Environment.

Hugging Face Spaces (Docker SDK, set `PORT=7860`) and Railway work the same way.

## How it works

```
PDF ─► Read ─► Extract ─► 4. Missing or conflicting info ─► Standard checks
    ─► 2. Duplicates ─► 1. Split invoices ─► 3. Partial delivery ─► Decision
```

**AI reads, rules decide.** The extractor only reports what is printed. Every decision comes from explicit rules in `app/pipeline.py`, so it can be explained and audited.

### Three outcomes

| Outcome | When |
|---|---|
| **Approved** | All four key checks and every standard check passed |
| **Needs review** | Information is missing, something is ambiguous, or a discrepancy is still open |
| **Rejected** | A confirmed violation of a business rule |

### Every decision explains itself
- **Why:** the main reason in one sentence, plus every other reason tagged with the check it came from.
- **Evidence:** the facts it relied on, such as earlier invoices on the PO, goods receipt numbers and ledger entries.
- **What remains uncertain:** for example a PO that was worked out rather than printed, values read from a scan, or a matching amount that may be a coincidence.
- **What happens next:** who acts (vendor, procurement, receiving, finance) and a drafted email to the vendor when something is needed from them.
- **Confidence:** High, Medium or Low.

### The four key checks (run and shown on every invoice)

| # | Check | Passes when | Needs review when | Rejected when |
|---|---|---|---|---|
| 1 | Split invoices | All invoices on the PO together stay within the ordered quantities | Bills from one vendor within 7 days add up past the approval limit, each one just under it | Billing goes beyond the PO quantity |
| 2 | Duplicate invoices | New file, new number for this vendor, no same-amount bill nearby (the same number from a *different* vendor is fine) | Same vendor and same amount within 7 days, under a different number | Same file, or same number for this vendor, even when written differently (BL-77120 = BL77120) |
| 3 | Partial delivery | Everything billed has been received, including partial deliveries billed exactly | Billed for goods that have not arrived yet | - |
| 4 | Missing or conflicting information | Required fields present, amounts agree, PO printed or clearly worked out | Missing number, date or total; amounts contradict; printed PO belongs to another vendor or does not exist; vendor name and tax ID disagree; scan unreadable | - |

### Standard checks (simple pass or fail)
Approved vendor, active vendor, bank account matches records, PO open, currency matches PO, every line on the PO, prices within tolerance, tax correct, invoice date valid, approval limit. A failure rejects the invoice and states exactly which check failed. The exception is the approval limit, which sends the invoice to a finance manager for review. These checks are shown as one line, "All 10 standard checks passed", and only appear in detail when one fails.

### Test invoices

| # | Group | Scenario | Result |
|---|---|---|---|
| 01 | Happy path | Clean invoice | Approved |
| 02 | 1. Split invoices | Third invoice on PO-1004, within what is left | Approved |
| 03 | 1. Split invoices | Bills 15 chairs after all 60 were already billed | Rejected |
| 04 | 1. Split invoices | $24,000 bill one day after a $24,500 bill on the same job, limit $25,000 | Needs review |
| 05 | 2. Duplicates | Paid in September, re-sent with a "COPY" stamp | Rejected |
| 06 | 2. Duplicates | Same invoice, number written as BL77120 instead of BL-77120 | Rejected |
| 07 | 2. Duplicates | New number, same amount as a bill from the day before | Needs review |
| 08 | 2. Duplicates | INV-1042 already used, but by a different vendor | Approved |
| 09 | 3. Partial delivery | Bills 25 monitors, 18 received | Needs review |
| 10 | 3. Partial delivery | 30 of 40 keyboards received, bills exactly 30 | Approved |
| 11 | 4. Missing or conflicting | No invoice number or date | Needs review |
| 12 | 4. Missing or conflicting | Lines add up to $2,880 but the subtotal says $2,980 | Needs review |
| 13 | 4. Missing or conflicting | No PO printed, worked out to PO-1008 | Approved |
| 14 | 4. Missing or conflicting | Printed PO belongs to another vendor, lines match PO-1007 | Needs review |
| 15 | 4. Missing or conflicting | Scanned paper invoice | Approved |
| 16 | Standard check | Bank account differs from records | Rejected |
| 17 | Standard check | Vendor not approved | Rejected |
| 18 | Standard check | Price 8.8% above the PO | Rejected |
| - | Duplicates | Any invoice uploaded twice | Rejected |

Reference data lives in `data/` as CSV files: vendors, POs and PO lines, goods receipts, and the invoice ledger of earlier invoices. Thresholds can be changed on the Rules page. A reviewer can approve or reject an invoice that needs review; a note is required and kept in the audit trail. Use "Reset demo data" before a live demo.

## Demo script (5 minutes)

1. Run **01**: all four key checks pass, and the standard checks show as one line. Walk through Why, Evidence, Uncertain and Next steps.
2. Run **01** again: rejected as a duplicate (same file).
3. Run **03**: split-invoice over-billing. Show the earlier invoices listed as evidence and the PO meter.
4. Run **09**: partial delivery, needs review, routed to receiving.
5. Run **14**: the printed PO conflicts with the lines. The app suggests the right PO but asks a person to confirm.
6. Run **04**: bills split under the approval limit. Approve it as the finance manager with a note and show the audit trail.
