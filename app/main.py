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
        for _ in range(1200):
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
    store.save_run(r)
    return slim(r)


@app.get("/api/stats")
def stats():
    rs = [r for r in store.list_runs() if r["status"] == "done"]
    by = Counter(r["final_status"] for r in rs)
    auto = sum(1 for r in rs if r["decision"] == "Approved")
    issues = Counter()
    for r in rs:
        for i in (r.get("result") or {}).get("issues", []):
            issues[i["code"]] += 1
    value = lambda st: round(sum(r["total"] or 0 for r in rs if r["final_status"] == st and r["doc_type"] != "credit_note"), 2)
    keys = {k: Counter() for k, _ in pipeline.KEY_CHECKS}
    for r in rs:
        for st in r.get("stages") or []:
            if st.get("id") in keys:
                keys[st["id"]][st.get("outcome", "na")] += 1
    key_stats = [{"id": k, "title": t, **{o: keys[k].get(o, 0) for o in ("pass", "review", "reject", "na")}}
                 for k, t in pipeline.KEY_CHECKS]
    return {
        "key_stats": key_stats,
        "processed": len(rs),
        "approved": by.get("Approved", 0), "on_hold": by.get("Needs review", 0), "rejected": by.get("Rejected", 0),
        "auto_rate": round(auto / len(rs) * 100) if rs else 0,
        "value_processed": round(sum(abs(r["total"] or 0) for r in rs), 2),
        "value_approved": value("Approved"), "value_on_hold": value("Needs review"), "value_blocked": value("Rejected"),
        "top_issues": issues.most_common(6),
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
