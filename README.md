# InvoSure

Takes a vendor invoice PDF and returns a reasoned decision: **Approved**, **Needs review** or **Rejected**. It shows every step as it runs, says who has to act next, and drafts the email to the vendor when something is missing.

Built for the AI Solutions Analyst case study, PS-1 (Finance / AP).

## Run it locally

```bash
pip install -r requirements.txt
sudo apt-get install -y tesseract-ocr     # macOS: brew install tesseract
uvicorn app.main:app --port 8000
```

Open http://localhost:8000. The 24 test invoices are already in `samples/`. To regenerate them, run `python scripts/make_samples.py`.

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

## What you see

- **Overview:** invoices processed, straight-through rate, high-confidence reads, exceptions with the money at risk. Also a chart of the straight-through rate over time, a decisions donut, extraction confidence buckets and recent documents with a confidence bar.
- **Live run:** the four key checks fill in as the invoice is processed, then the decision explains why, the evidence, what is uncertain and what happens next.
- **Audit report:** a score out of 100, counts of passed checks, warnings, errors and notes, finding cards with a "Fix manually" button, and every passed check.
- **Extracted data:** every field with its confidence and the reason for it, for example "Cross-checked: lines + tax = total" or "Not printed; worked out from the lines and amount". Edit any field and re-run every check on the corrected values. Also shown as a Table and JSON.
- **Invoice timeline:** a timestamped record of everything that happened, including corrections and reviewer decisions.
- **Same-time processing:** only one invoice at a time may read the billing history and record its decision. Two $15,000 invoices against a PO with $20,000 left, sent together: one is approved, the other rejected. Use the "Run both at the same time" button.
- **Vendors and POs:** add a vendor or a purchase order (with lines and received quantities) from the "Vendors and POs" page. When an invoice is rejected because its supplier or PO is not on file, click **"Set up from this invoice"**: the forms are pre-filled from what was read, and one click saves them and checks the invoice again. Records added in the app are marked "Added here", can be removed, and are cleared by "Reset demo data".
- **Rule simulation:** change a threshold on the Rules page and click "Simulate before saving". Every processed invoice is re-decided in the order it arrived, and the page shows which decisions would change, for example "With these rules, 1 more invoice would have been approved automatically".

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
| 01–07 | Clean invoices | Seven vendors and four invoice layouts, all matching their PO, delivered and new | Approved |
| 08 | 1. Split invoices | Third invoice on PO-1004, within what is left | Approved |
| 09 | 1. Split invoices | Bills 15 chairs after all 60 were already billed | Rejected |
| 10 | 1. Split invoices | $24,000 bill one day after a $24,500 bill on the same job, limit $25,000 | Needs review |
| 11 | 2. Duplicates | Paid in September, re-sent with a "COPY" stamp | Rejected |
| 12 | 2. Duplicates | Same invoice, number written as BL77120 instead of BL-77120 | Rejected |
| 13 | 2. Duplicates | New number, same amount as a bill from the day before | Needs review |
| 14 | 2. Duplicates | INV-1042 already used, but by a different vendor | Approved |
| 15 | 3. Partial delivery | Bills 25 monitors, 18 received | Needs review |
| 16 | 3. Partial delivery | 30 of 40 keyboards received, bills exactly 30 | Approved |
| 17 | 4. Missing or conflicting | No invoice number or date (fix them by hand and it becomes Approved) | Needs review |
| 18 | 4. Missing or conflicting | Lines add up to $2,880 but the subtotal says $2,980 | Needs review |
| 19 | 4. Missing or conflicting | No PO printed, worked out to PO-1008 | Approved |
| 20 | 4. Missing or conflicting | Printed PO belongs to another vendor, lines match PO-1007 | Needs review |
| 21 | 4. Missing or conflicting | Scanned paper invoice | Approved |
| 22 + 23 | Same-time processing | Two $15,000 invoices against $20,000 left, sent together | One approved, one rejected |
| 24 | Standard check | Bank account differs from records | Rejected |
| 25 | Standard check | Vendor not approved | Rejected |
| 26 | Standard check | Price 8.8% above the PO | Rejected |
| - | Duplicates | Any invoice uploaded twice | Rejected |

Reference data lives in `data/` as CSV files: vendors, POs and PO lines, goods receipts, and the invoice ledger of earlier invoices. Thresholds can be changed on the Rules page. A reviewer can approve or reject an invoice that needs review; a note is required and kept in the audit trail. Use "Reset demo data" before a live demo.

## Demo script (5 minutes)

1. **Rules → Reset demo data.**
2. **Process invoice → 01.** All key checks pass. Show the audit score of 100, field confidence and the timeline.
3. **01 again:** rejected, because it is the same file.
4. **Run both at the same time (22 + 23):** two $15,000 invoices against $20,000 left. One is approved; the other waits, re-reads the PO balance and is rejected.
5. **15:** partial delivery, needs review. Read Why, Evidence and Uncertain.
6. **17:** missing number and date. Click "Fix manually", type them in, re-run, and it becomes Approved. The timeline records the correction.
7. **Rules:** set price tolerance to 10% and $500, click "Simulate before saving", and show that invoice 26 would now be approved. Don't save.
8. **Your own invoice:** upload any invoice PDF. It is rejected as an unknown vendor. Click "Set up from this invoice", check the values, save, and it is checked again.
9. **Overview:** the dashboard now reflects everything you ran.
