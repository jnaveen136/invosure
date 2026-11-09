"""HTTP API + static UI.

Start with:  uvicorn app.main:app --port 8000
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import extract, pipeline, store

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
STAGE_DELAY = float(os.environ.get("STAGE_DELAY", "0.45"))

app = FastAPI(title="InvoSure")
store.init_db()
# A run left "running" from before a restart (for example a server that ran out of memory) will never
# finish on its own, so close it with a clear message instead of leaving the screen spinning.
for _r in store.list_runs():
    if _r["status"] != "done":
        pipeline.finish_interrupted(_r, "The server restarted while this invoice was being processed.")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.middleware("http")
async def no_stale_assets(request, call_next):
    """Make the browser re-check CSS/JS on every load, so an update is never hidden by an old cached copy."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/", response_class=HTMLResponse)
def index():
    html = (ROOT / "static" / "index.html").read_text()
    for name in ("styles.css", "app.js"):  # version the asset URLs by file time
        v = int((ROOT / "static" / name).stat().st_mtime)
        html = html.replace(f"/static/{name}", f"/static/{name}?v={v}")
    return html


def start_run(path: Path, filename: str) -> str:
    run_id = uuid.uuid4().hex[:10]
    dest = store.UPLOADS / f"{run_id}.pdf"
    raw = path.read_bytes() if isinstance(path, Path) else path
    dest.write_bytes(raw)
    run = {"id": run_id, "created_at": datetime.now().isoformat(timespec="seconds"), "filename": filename,
           "file_path": str(dest), "file_hash": hashlib.sha256(raw).hexdigest(), "status": "running",
           "meta": {"timeline": [{"at": datetime.now().isoformat(timespec="seconds"), "text": f"Invoice received: {filename}", "kind": "info"}]},
           "stages": [{"id": s, "name": n, "status": "pending"} for s, n in pipeline.STAGES]}
    store.save_run(run)
    threading.Thread(target=pipeline.run_pipeline, args=(run, STAGE_DELAY), daemon=True).start()
    return run_id


@app.post("/api/runs")
async def upload(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(413, "File too large (15 MB max)")
    tmp = store.UPLOADS / f"tmp-{uuid.uuid4().hex}"
    tmp.write_bytes(data)
    try:
        run_id = start_run(tmp, file.filename or "invoice.pdf")
    finally:
        tmp.unlink(missing_ok=True)
    return {"id": run_id}


class SampleReq(BaseModel):
    file: str


@app.post("/api/runs/sample")
def run_sample(req: SampleReq):
    p = (SAMPLES / req.file).resolve()
    if p.parent != SAMPLES.resolve() or not p.exists():
        raise HTTPException(404, "Unknown sample")
    return {"id": start_run(p, req.file)}


class BatchReq(BaseModel):
    files: list[str]


@app.post("/api/runs/batch")
def run_batch(req: BatchReq):
    """Start several test invoices at the same moment (used to show safe same-time processing)."""
    paths = []
    for f in req.files[:6]:
        p = (SAMPLES / f).resolve()
        if p.parent != SAMPLES.resolve() or not p.exists():
            raise HTTPException(404, f"Unknown sample {f}")
        paths.append(p)
    return {"ids": [start_run(p, p.name) for p in paths]}


class CorrectReq(BaseModel):
    fields: dict


@app.post("/api/runs/{run_id}/correct")
def correct(run_id: str, req: CorrectReq):
    """Apply a reviewer's corrections to extracted fields and run every check again."""
    r = store.get_run(run_id)
    if not r or r["status"] != "done":
        raise HTTPException(404, "Invoice not found or still running")
    allowed = {k for k, _, _ in pipeline.EDITABLE_FIELDS}
    changes = {k: v for k, v in req.fields.items() if k in allowed}
    if not changes:
        raise HTTPException(400, "Nothing to correct")
    meta = r.get("meta") or {}
    if not meta.get("extracted"):
        raise HTTPException(400, "This invoice was processed before corrections were supported; process it again")
    meta.setdefault("overrides", {}).update(changes)
    meta["reuse"] = True
    names = ", ".join(lbl.lower() for k, lbl, _ in pipeline.EDITABLE_FIELDS if k in changes)
    meta.setdefault("timeline", []).append({"at": datetime.now().isoformat(timespec="seconds"),
                                            "text": f"Corrected by reviewer: {names}. All checks run again.", "kind": "info"})
    if r.get("review"):
        meta.setdefault("timeline", []).append({"at": datetime.now().isoformat(timespec="seconds"),
                                                "text": "Earlier reviewer decision cleared because the data changed.", "kind": "warn"})
    r.update(meta=meta, review=None, status="running", decision=None, final_status=None)
    store.save_run(r)
    threading.Thread(target=pipeline.run_pipeline, args=(r, min(STAGE_DELAY, 0.15)), daemon=True).start()
    return {"id": run_id}


@app.post("/api/runs/{run_id}/recheck")
def recheck(run_id: str):
    """Run every check again on the values already read, e.g. after adding the vendor or PO it needed."""
    r = store.get_run(run_id)
    if not r or r["status"] != "done":
        raise HTTPException(404, "Invoice not found or still running")
    meta = r.get("meta") or {}
    if not meta.get("extracted"):
        raise HTTPException(400, "This invoice was processed before re-checks were supported; process it again")
    meta["reuse"] = True
    meta.setdefault("timeline", []).append({"at": datetime.now().isoformat(timespec="seconds"),
                                            "text": "Checked again after vendor or purchase order records were updated.", "kind": "info"})
    r.update(meta=meta, review=None, status="running", decision=None, final_status=None)
    store.save_run(r)
    threading.Thread(target=pipeline.run_pipeline, args=(r, min(STAGE_DELAY, 0.15)), daemon=True).start()
    return {"id": run_id}


# =========================================================================== vendors and purchase orders


class VendorReq(BaseModel):
    name: str
    tax_id: str = ""
    bank_account: str = ""
    bank_routing: str = ""
    currency: str = "USD"
    payment_terms_days: int = 30
    contact_email: str = ""
    phone: str = ""


@app.post("/api/vendors")
def add_vendor(req: VendorReq):
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "Vendor name is required")
    ref = store.reference()
    norm = lambda x: "".join(ch for ch in (x or "").upper() if ch.isalnum())
    for v in ref["vendors"].values():
        if v["name"].strip().lower() == name.lower():
            raise HTTPException(400, f"{v['name']} is already in the vendor list ({v['vendor_id']})")
        if req.tax_id.strip() and norm(v["tax_id"]) == norm(req.tax_id):
            raise HTTPException(400, f"Tax ID {req.tax_id} already belongs to {v['name']}")
    nums = [int("".join(ch for ch in vid if ch.isdigit()) or 0) for vid in ref["vendors"]]
    vid = f"V{max(nums + [0]) + 1:03d}"
    vendor = {"vendor_id": vid, "name": name, "aliases": "", "tax_id": req.tax_id.strip(), "status": "active",
              "bank_account": "".join(ch for ch in req.bank_account if ch.isalnum()).upper(),
              "bank_routing": req.bank_routing.strip().upper(), "currency": (req.currency or "USD").strip().upper(),
              "payment_terms_days": str(max(0, req.payment_terms_days)), "contact_email": req.contact_email.strip(),
              "phone": req.phone.strip()}
    added = store.load_added()
    added["vendors"].append(vendor)
    store.save_added(added)
    return {**vendor, "added": True}


class POLine(BaseModel):
    description: str
    qty: float
    unit_price: float
    line_type: str = "goods"
    received: float = 0


class POReq(BaseModel):
    po_number: str = ""
    vendor_id: str
    po_date: str = ""
    currency: str = ""
    buyer: str = ""
    lines: list[POLine]


@app.get("/api/pos/next-number")
def next_po_number():
    nums = [int("".join(ch for ch in p if ch.isdigit()) or 0) for p in store.reference()["pos"]]
    return {"po_number": f"PO-{max(nums + [2000]) + 1}"}


@app.post("/api/pos")
def add_po(req: POReq):
    ref = store.reference()
    vendor = ref["vendors"].get(req.vendor_id)
    if not vendor:
        raise HTTPException(400, "Choose a vendor from the list")
    digits = "".join(ch for ch in req.po_number if ch.isdigit())
    po_number = f"PO-{digits}" if digits else next_po_number()["po_number"]
    if po_number in ref["pos"]:
        raise HTTPException(400, f"{po_number} already exists")
    lines = [l for l in req.lines if l.description.strip()]
    if not lines:
        raise HTTPException(400, "Add at least one line")
    for l in lines:
        if l.qty <= 0 or l.unit_price < 0 or l.received < 0:
            raise HTTPException(400, f"Check the numbers on \"{l.description}\"")
        if l.received > l.qty:
            raise HTTPException(400, f"\"{l.description}\": received cannot be more than ordered")
    today = store.today().isoformat()
    added = store.load_added()
    added["pos"].append({"po_number": po_number, "vendor_id": vendor["vendor_id"], "po_date": req.po_date or today,
                         "currency": (req.currency or vendor.get("currency") or "USD").upper(), "status": "open",
                         "buyer": req.buyer.strip() or "AP team"})
    grn = f"GRN-{9000 + len(added['receipts']) + 1}"
    for i, l in enumerate(lines, 1):
        line_type = "service" if l.line_type == "service" else "goods"
        added["lines"].append({"po_number": po_number, "line_no": i, "description": l.description.strip(),
                               "qty": l.qty, "unit_price": l.unit_price, "line_type": line_type})
        if line_type == "goods" and l.received > 0:
            added["receipts"].append({"grn_number": grn, "po_number": po_number, "line_no": i,
                                      "qty_received": l.received, "received_date": today})
    store.save_added(added)
    return {"po_number": po_number}


@app.delete("/api/pos/{po_number}")
def delete_po(po_number: str):
    added = store.load_added()
    if not any(p["po_number"] == po_number for p in added["pos"]):
        raise HTTPException(400, "Only purchase orders added in the app can be removed")
    added["pos"] = [p for p in added["pos"] if p["po_number"] != po_number]
    added["lines"] = [l for l in added["lines"] if l["po_number"] != po_number]
    added["receipts"] = [g for g in added["receipts"] if g["po_number"] != po_number]
    store.save_added(added)
    return {"ok": True}


@app.delete("/api/vendors/{vendor_id}")
def delete_vendor(vendor_id: str):
    added = store.load_added()
    if not any(v["vendor_id"] == vendor_id for v in added["vendors"]):
        raise HTTPException(400, "Only vendors added in the app can be removed")
    if any(p["vendor_id"] == vendor_id for p in added["pos"]):
        raise HTTPException(400, "Remove this vendor's purchase orders first")
    added["vendors"] = [v for v in added["vendors"] if v["vendor_id"] != vendor_id]
    store.save_added(added)
    return {"ok": True}


@app.post("/api/simulate")
def simulate(body: dict):
    return pipeline.simulate(body)


@app.get("/api/samples")
def samples():
    return json.loads((SAMPLES / "index.json").read_text())


@app.get("/api/samples/{name}")
def sample_file(name: str):
    p = (SAMPLES / name).resolve()
    if p.parent != SAMPLES.resolve() or not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="application/pdf")


def slim(r):
    return {k: r.get(k) for k in ("id", "created_at", "filename", "status", "decision", "final_status", "route",
                                  "vendor_name", "invoice_number", "invoice_date", "po_number", "total",
                                  "currency", "doc_type", "extractor", "review")}


@app.get("/api/runs")
def runs():
    return [slim(r) for r in store.list_runs()]


@app.get("/api/runs/{run_id}")
def run(run_id: str):
    r = store.get_run(run_id)
    if not r:
        raise HTTPException(404)
    r.pop("file_path", None)
    return r


@app.get("/api/runs/{run_id}/file")
def run_file(run_id: str):
    r = store.get_run(run_id)
    if not r:
        raise HTTPException(404)
    return FileResponse(r["file_path"], media_type="application/pdf")


@app.get("/api/runs/{run_id}/page/{n}.png")
def run_page(run_id: str, n: int):
    """Render a page as PNG so the preview works in every browser, including mobile."""
    import io

    import pypdfium2 as pdfium
    from fastapi.responses import Response

    r = store.get_run(run_id)
    if not r:
        raise HTTPException(404)
    try:
        doc = pdfium.PdfDocument(r["file_path"])
        if n < 1 or n > len(doc):
            raise HTTPException(404)
        img = doc[n - 1].render(scale=1.6).to_pil()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, "Could not render this PDF")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/runs/{run_id}/pages")
def run_pages(run_id: str):
    import pypdfium2 as pdfium

    r = store.get_run(run_id)
    if not r:
        raise HTTPException(404)
    try:
        return {"pages": len(pdfium.PdfDocument(r["file_path"]))}
    except Exception:
        return {"pages": 0}


@app.get("/api/runs/{run_id}/stream")
async def stream(run_id: str):
    async def gen():
        last = None
        for _ in range(4000):
            r = store.get_run(run_id)
            if not r:
                yield "event: error\ndata: {}\n\n"
                return
            r.pop("file_path", None)
            payload = json.dumps(r)
            if payload != last:
                yield f"data: {payload}\n\n"
                last = payload
            if r["status"] == "done":
                yield "event: done\ndata: {}\n\n"
                return
            await asyncio.sleep(0.15)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


class ReviewReq(BaseModel):
    action: str  # approve | reject
    note: str = ""
    reviewer: str = "AP reviewer"


@app.post("/api/runs/{run_id}/review")
def review(run_id: str, req: ReviewReq):
    r = store.get_run(run_id)
    if not r or r["status"] != "done":
        raise HTTPException(404)
    if req.action not in ("approve", "reject"):
        raise HTTPException(400, "action must be approve or reject")
    if not req.note.strip():
        raise HTTPException(400, "A note is required for the audit trail")
    r["final_status"] = "Approved" if req.action == "approve" else "Rejected"
    r["review"] = {"action": req.action, "note": req.note.strip(), "by": req.reviewer,
                   "at": datetime.now().isoformat(timespec="seconds"), "from": r["decision"]}
    meta = r.get("meta") or {}
    meta.setdefault("timeline", []).append({"at": r["review"]["at"], "kind": "pass" if req.action == "approve" else "fail",
                                            "text": f"{'Approved' if req.action == 'approve' else 'Rejected'} by {req.reviewer}: {req.note.strip()}"})
    r["meta"] = meta
    store.save_run(r)
    return slim(r)


def run_confidence(r):
    for st in r.get("stages") or []:
        if st.get("id") == "decide" and isinstance(st.get("data"), dict):
            return st["data"].get("extraction_confidence")
    return None


@app.get("/api/stats")
def stats():
    rs = sorted([r for r in store.list_runs() if r["status"] == "done"], key=lambda r: r["created_at"])
    n = len(rs)
    by = Counter(r["final_status"] for r in rs)
    sys_by = Counter(r["decision"] for r in rs)
    confs = [run_confidence(r) for r in rs]
    buckets = [("95-100%", 95, 101), ("80-94%", 80, 95), ("50-79%", 50, 80), ("0-49%", 0, 50)]
    dist = [{"label": b, "count": sum(1 for x in confs if x is not None and lo <= x < hi)} for b, lo, hi in buckets]
    series, auto = [], 0
    for i, r in enumerate(rs, 1):  # straight-through rate as invoices came in
        auto += r["decision"] == "Approved"
        series.append({"n": i, "rate": round(auto / i * 100, 1), "file": r["filename"], "status": r["decision"]})
    at_risk = [r for r in rs if r["final_status"] in ("Needs review", "Rejected")]
    recent = []
    for r in reversed(rs[-8:]):
        recent.append({"id": r["id"], "invoice_number": r["invoice_number"], "vendor_name": r["vendor_name"],
                       "invoice_date": r["invoice_date"], "total": r["total"], "currency": r["currency"],
                       "final_status": r["final_status"], "confidence": run_confidence(r), "filename": r["filename"]})
    days = [r["created_at"][:10] for r in rs]
    return {
        "processed": n,
        "approved": by.get("Approved", 0), "on_hold": by.get("Needs review", 0), "rejected": by.get("Rejected", 0),
        "decisions": {"Approved": sys_by.get("Approved", 0), "Needs review": sys_by.get("Needs review", 0), "Rejected": sys_by.get("Rejected", 0)},
        "auto_rate": round(sys_by.get("Approved", 0) / n * 100) if n else 0,
        "high_conf": dist[0]["count"],
        "exceptions": len(at_risk),
        "value_at_risk": round(sum(r["total"] or 0 for r in at_risk), 2),
        "value_approved": round(sum(r["total"] or 0 for r in rs if r["final_status"] == "Approved"), 2),
        "avg_confidence": round(sum(x for x in confs if x is not None) / max(1, sum(1 for x in confs if x is not None)), 1),
        "series": series, "confidence_dist": dist, "recent": recent,
        "first_day": days[0] if days else None, "last_day": days[-1] if days else None,
        "extractor": f"Claude ({extract.CLAUDE_MODEL})" if extract.claude_available() else "Built-in parser",
    }


@app.get("/api/reference")
def reference():
    ref = store.reference()
    history = [r for r in store.list_runs() if r["status"] == "done"]
    ctx = type("C", (), {"ref": ref, "history": history})()
    out = []
    for po in ref["pos"].values():
        billed = pipeline.billed_so_far(ctx, po["po_number"])
        lines = []
        for l in po["lines"]:
            recv = sum(g["qty_received"] for g in ref["receipts"] if g["po_number"] == po["po_number"] and g["line_no"] == l["line_no"])
            lines.append({**l, "billed": billed.get(l["line_no"], 0), "received": recv})
        billed_value = sum(l["billed"] * l["unit_price"] for l in lines)
        out.append({**{k: po[k] for k in ("po_number", "vendor_id", "po_date", "currency", "status", "buyer", "total")},
                    "added": bool(po.get("added")),
                    "vendor_name": ref["vendors"][po["vendor_id"]]["name"], "lines": lines,
                    "billed_value": round(billed_value, 2),
                    "pct": round(billed_value / po["total"] * 100, 1) if po["total"] else 0})
    return {"pos": sorted(out, key=lambda p: p["po_number"]), "vendors": list(ref["vendors"].values())}


@app.get("/api/rules")
def get_rules():
    rules = store.load_rules()
    return [{"key": k, "value": v, "label": store.RULE_LABELS[k][0], "help": store.RULE_LABELS[k][1]} for k, v in rules.items()]


@app.put("/api/rules")
def put_rules(body: dict):
    store.save_rules(body)
    return get_rules()


@app.post("/api/reset")
def reset():
    store.reset_runs()
    return {"ok": True}
