"""Reference data (CSV) and run history (SQLite)."""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import threading
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STORAGE = Path(os.environ.get("STORAGE_DIR", ROOT / "storage"))
UPLOADS = STORAGE / "uploads"
DB_PATH = STORAGE / "runs.db"
RULES_PATH = STORAGE / "rules.json"
STORAGE.mkdir(parents=True, exist_ok=True)
UPLOADS.mkdir(parents=True, exist_ok=True)

DEFAULT_RULES = {
    "price_tolerance_pct": 2.0,
    "price_tolerance_abs": 100.0,
    "approval_limit": 25000.0,
    "near_duplicate_days": 7,
    "max_invoice_age_days": 180,
    "ocr_min_quality": 70.0,
    "vendor_match_threshold": 85,
}

RULE_LABELS = {
    "price_tolerance_pct": ("Price tolerance (%)", "Billed amount may differ from the PO by up to this percent of the billed lines."),
    "price_tolerance_abs": ("Price tolerance (absolute)", "...and by no more than this amount. The lower of the two limits applies."),
    "approval_limit": ("Approval limit", "Invoices at or above this total need a finance manager's sign-off, even when everything matches."),
    "near_duplicate_days": ("Near-duplicate window (days)", "Same vendor and same amount within this many days is flagged as a possible duplicate."),
    "max_invoice_age_days": ("Maximum invoice age (days)", "Older invoices are flagged as stale."),
    "ocr_min_quality": ("Minimum scan quality (%)", "Scanned invoices read below this confidence go to a person to verify."),
    "vendor_match_threshold": ("Vendor name match (%)", "How closely the printed name must match the vendor master."),
}


def today() -> date:
    v = os.environ.get("TODAY")
    return date.fromisoformat(v) if v else date.today()


def load_rules() -> dict:
    rules = dict(DEFAULT_RULES)
    if RULES_PATH.exists():
        rules.update(json.loads(RULES_PATH.read_text()))
    return rules


def save_rules(new: dict) -> dict:
    rules = load_rules()
    for k, v in new.items():
        if k in DEFAULT_RULES:
            rules[k] = type(DEFAULT_RULES[k])(v)
    RULES_PATH.write_text(json.dumps(rules, indent=2))
    return rules


def _csv(name):
    with open(DATA / name, newline="") as f:
        return list(csv.DictReader(f))


def reference() -> dict:
    vendors = {r["vendor_id"]: r for r in _csv("vendors.csv")}
    pos = {r["po_number"]: {**r, "lines": []} for r in _csv("purchase_orders.csv")}
    for ln in _csv("po_lines.csv"):
        ln["qty"] = float(ln["qty"])
        ln["unit_price"] = float(ln["unit_price"])
        ln["line_no"] = int(ln["line_no"])
        pos[ln["po_number"]]["lines"].append(ln)
    for p in pos.values():
        p["total"] = round(sum(l["qty"] * l["unit_price"] for l in p["lines"]), 2)
    receipts = _csv("goods_receipts.csv")
    for r in receipts:
        r["line_no"] = int(r["line_no"])
        r["qty_received"] = float(r["qty_received"])
    ledger = _csv("invoice_ledger.csv")
    for r in ledger:
        r["qty"] = float(r["qty"])
        r["amount"] = float(r["amount"])
        r["po_line"] = int(r["po_line"])
    return {"vendors": vendors, "pos": pos, "receipts": receipts, "ledger": ledger}


# --------------------------------------------------------------------------- run history

_lock = threading.Lock()


def db():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with _lock, db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, created_at TEXT, filename TEXT, file_path TEXT, file_hash TEXT,
            status TEXT, decision TEXT, final_status TEXT, route TEXT,
            vendor_id TEXT, vendor_name TEXT, invoice_number TEXT, invoice_date TEXT,
            po_number TEXT, total REAL, currency TEXT, doc_type TEXT, extractor TEXT,
            stages TEXT, result TEXT, review TEXT)""")


def save_run(run: dict):
    cols = ["id", "created_at", "filename", "file_path", "file_hash", "status", "decision", "final_status", "route",
            "vendor_id", "vendor_name", "invoice_number", "invoice_date", "po_number", "total", "currency",
            "doc_type", "extractor", "stages", "result", "review"]
    vals = []
    for c in cols:
        v = run.get(c)
        vals.append(json.dumps(v) if c in ("stages", "result", "review") else v)
    with _lock, db() as con:
        con.execute(f"INSERT OR REPLACE INTO runs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)


def _row(r):
    d = dict(r)
    for c in ("stages", "result", "review"):
        d[c] = json.loads(d[c]) if d[c] else None
    return d


def get_run(run_id):
    with db() as con:
        r = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    return _row(r) if r else None


def list_runs():
    with db() as con:
        rows = con.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
    return [_row(r) for r in rows]


def reset_runs():
    with _lock, db() as con:
        con.execute("DELETE FROM runs")
    for f in UPLOADS.glob("*"):
        f.unlink()
    if RULES_PATH.exists():
        RULES_PATH.unlink()
