"""FINN API  ·  run:  uvicorn main:app --host 0.0.0.0 --port 8000      (from the backend/ directory)

GET  /api/health        liveness + which LLM provider/keys are configured
GET  /api/portfolio     16 portfolio tickers with latest EOD close, 1D %, 20D volume ratio
GET  /api/push          unified Push feed (digest + triggered alerts); thresholds are query params
GET  /api/pull/{ticker} on-demand research for ANY NSE ticker (bare or .NS), SQLite-cached
GET  /api/telemetry     LLM routing ledger + cache savings
POST /api/prewarm       warm demo tickers (+ optional portfolio) and export data/fallback_bundle.json
GET  /api/prewarm/{id}  prewarm job status        GET /api/fallback-bundle  the exported static snapshot

Design notes
  • Handlers are plain `def` → FastAPI runs them in its threadpool, so slow NSE/Screener/LLM I/O never blocks the event loop.
  • /api/push is memoised for 120s per parameter set and falls back to the last good payload on upstream failure.
  • /api/pull is cached in SQLite by pull_engine (12h TTL, one in-flight generation per ticker, stale-if-error).
  • CORS is open by default (read-only public-data API); restrict with FINN_CORS="https://my-app.vercel.app,http://localhost:5173".
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import data_fetcher as df
import llm_router as llm
import pull_engine as pull
import push_engine
from schemas import DEFAULT_PORTFOLIO

log = logging.getLogger("finn.api")
logging.basicConfig(level=os.getenv("FINN_LOG", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="FINN – Financial Investment Agent API", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in os.getenv("FINN_CORS", "*").split(",") if o.strip()],
                   allow_credentials=False, allow_methods=["*"], allow_headers=["*"], expose_headers=["X-FINN-Stale", "X-FINN-Cache"])

BUNDLE_PATH = Path(df.DATA_DIR) / "fallback_bundle.json"
DEFAULT_PREWARM = [t.strip().upper() for t in os.getenv("FINN_PREWARM", "DIXON,BEL,TRENT,TITAN,PERSISTENT").split(",") if t.strip()]
_KEY_ENV = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ───────────────────────── health ─────────────────────────
@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "time": _now(), "provider": llm.PROVIDER, "llm_key_present": bool(os.getenv(_KEY_ENV.get(llm.PROVIDER, ""))),
            "models": {t: llm.model_for(t) for t in ("fast", "deep")}, "fallback_bundle": BUNDLE_PATH.exists(), "portfolio_size": len(DEFAULT_PORTFOLIO)}


# ───────────────────────── portfolio ─────────────────────────
def portfolio_payload() -> dict:
    snaps = df.get_eod_batch([s.symbol for s in DEFAULT_PORTFOLIO])  # order-preserving; disk-cached 15 min; never raises
    items = []
    for st, sn in zip(DEFAULT_PORTFOLIO, snaps):
        items.append({"symbol": st.symbol, "base": st.base, "name": st.name, "sector": st.sector, "cap": st.cap, "as_of": sn.as_of or None,
                      "close": sn.close or None, "return_1d_pct": sn.return_1d_pct if not (sn.error and not sn.close) else None,
                      "volume_ratio": sn.volume_ratio if not (sn.error and not sn.close) else None, "volume": sn.volume,
                      "dist_from_52w_high_pct": sn.dist_from_52w_high_pct, "dist_from_52w_low_pct": sn.dist_from_52w_low_pct,
                      "stale": bool(sn.error and sn.close), "error": sn.error})
    dates = [i["as_of"] for i in items if i["as_of"]]
    return {"generated_at": _now(), "as_of": max(dates) if dates else None, "count": len(items), "ok": sum(1 for i in items if i["close"]), "items": items}


@app.get("/api/portfolio")
def portfolio() -> dict:
    return portfolio_payload()


# ───────────────────────── push ─────────────────────────
_push_memo: dict[tuple, tuple[float, dict]] = {}
_push_lock = threading.Lock()
PUSH_TTL_S = 120


def push_payload(window_hours: int, price_thresh: float, vol_mult: float, symbols: Optional[list[str]] = None, near_52w: float = 0.0,
                 use_llm: bool = True, refresh: bool = False) -> tuple[dict, bool]:
    """→ (payload, stale). Serialised so concurrent identical requests share one build (NSE rate-limits hard)."""
    key = (window_hours, price_thresh, vol_mult, tuple(symbols or ()), near_52w, use_llm)
    with _push_lock:
        hit = _push_memo.get(key)
        if hit and not refresh and time.time() - hit[0] < PUSH_TTL_S:
            return hit[1], False
        try:
            feed = push_engine.build_push_feed(window_hours, price_thresh_pct=price_thresh, vol_spike_mult=vol_mult, symbols=symbols,
                                               near_52w_pct=near_52w, use_llm=use_llm)
            data = feed.model_dump(mode="json")
            _push_memo[key] = (time.time(), data)
            return data, False
        except Exception as e:  # noqa: BLE001
            log.exception("push build failed")
            if hit:
                return hit[1], True
            raise HTTPException(502, f"Push feed unavailable: {str(e)[:160]}") from e


@app.get("/api/push")
def push(window_hours: int = Query(24, ge=1, le=720, description="look-back window, hours"),
         price_thresh: float = Query(5.0, gt=0, le=50, description="|1D move| % that triggers a price alert"),
         vol_mult: float = Query(2.0, gt=0, le=50, description="volume ÷ 20D average that triggers a volume alert"),
         near_52w: float = Query(0.0, ge=0, le=20, description="alert when within X% of the 52-week high/low (0 = off)"),
         symbols: Optional[str] = Query(None, description="comma-separated tickers; default = demo portfolio"),
         use_llm: bool = True, refresh: bool = False) -> JSONResponse:
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else None
    data, stale = push_payload(window_hours, price_thresh, vol_mult, syms, near_52w, use_llm, refresh)
    return JSONResponse(data, headers={"X-FINN-Stale": "1"} if stale else {})


# ───────────────────────── pull ─────────────────────────
@app.get("/api/pull/{ticker}")
def pull_ticker(ticker: str, refresh: bool = False, use_llm: bool = True) -> JSONResponse:
    """Works for portfolio stocks and any NSE ticker Screener covers: DIXON, dixon, TITAN.NS, M&M …"""
    try:
        rep = pull.get_report(ticker, refresh=refresh, use_llm=use_llm)
    except pull.InvalidTicker as e:
        raise HTTPException(400, str(e)) from e
    except pull.NoDataError as e:
        raise HTTPException(404, f"No primary fundamentals found for '{ticker}': {e}") from e
    except pull.PullError as e:
        raise HTTPException(502, str(e)) from e
    return JSONResponse(rep.model_dump(mode="json"), headers={"X-FINN-Cache": "hit" if rep.cache_hit else "miss"})


# ───────────────────────── telemetry ─────────────────────────
@app.get("/api/telemetry")
def telemetry() -> dict:
    stats, pc = llm.get_routing_stats(), pull.cache_stats()
    return {"generated_at": _now(), "llm": stats, "pull_cache": pc,
            "total_saved_usd": round(stats.get("est_cost_saved_by_cache_usd", 0.0) + pc["saved_usd"], 5),
            "total_cost_usd": stats.get("total_cost_usd", 0.0)}


# ───────────────────────── prewarm + static fallback bundle ─────────────────────────
class PrewarmReq(BaseModel):
    tickers: Optional[list[str]] = None  # default: FINN_PREWARM env or DIXON,BEL,TRENT,TITAN,PERSISTENT
    include_portfolio: bool = False  # also pull-analyse the 16 portfolio stocks (costs LLM calls)
    include_push: bool = True  # snapshot the default Push feed into the bundle
    refresh: bool = False  # bypass the pull cache


_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _write_bundle(bundle: dict) -> list[str]:
    paths = [BUNDLE_PATH] + ([Path(os.environ["FINN_BUNDLE_OUT"])] if os.getenv("FINN_BUNDLE_OUT") else [])  # e.g. ../frontend/public/fallback_bundle.json
    written = []
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(bundle, ensure_ascii=False))
        tmp.replace(p)  # atomic: a reader never sees a half-written file
        written.append(str(p))
    return written


def _run_prewarm(job_id: str, req: PrewarmReq) -> None:
    job = _jobs[job_id]
    tickers = list(dict.fromkeys([pull_t for t in (req.tickers or DEFAULT_PREWARM) if (pull_t := t.strip().upper())]))
    if req.include_portfolio:
        tickers += [s.base for s in DEFAULT_PORTFOLIO if s.base not in tickers]
    try:
        old = json.loads(BUNDLE_PATH.read_text()) if BUNDLE_PATH.exists() else {}
    except Exception:  # noqa: BLE001
        old = {}
    bundle = {"schema": 1, "generated_at": _now(), "portfolio": None, "push": None, "pulls": dict(old.get("pulls", {})), "errors": {}}  # keep last-good pulls
    job.update(total=len(tickers), status="running")
    try:
        bundle["portfolio"] = portfolio_payload()
    except Exception as e:  # noqa: BLE001
        bundle["portfolio"] = old.get("portfolio")
        bundle["errors"]["portfolio"] = str(e)[:160]
    if req.include_push:
        try:
            bundle["push"] = push_payload(24, 5.0, 2.0, None, 0.0, True, True)[0]
        except Exception as e:  # noqa: BLE001
            bundle["push"] = old.get("push")
            bundle["errors"]["push"] = str(getattr(e, "detail", e))[:160]
    for t in tickers:
        t0 = time.time()
        try:
            rep = pull.get_report(t, refresh=req.refresh)
            bundle["pulls"][rep.symbol.removesuffix(".NS")] = rep.model_dump(mode="json")
            job["results"][t] = {"ok": True, "mode": rep.mode, "cache_hit": rep.cache_hit, "llm_degraded": rep.llm_degraded,
                                 "cost_usd": rep.llm_cost_usd, "seconds": round(time.time() - t0, 1)}
        except Exception as e:  # noqa: BLE001
            msg = str(getattr(e, "detail", e))[:160]
            bundle["errors"][t] = msg
            job["results"][t] = {"ok": False, "error": msg, "seconds": round(time.time() - t0, 1)}
        job["done"] = len(job["results"])
    try:
        job["bundle_files"] = _write_bundle(bundle)
        job["status"] = "done"
    except Exception as e:  # noqa: BLE001
        job.update(status="failed", error=str(e)[:200])
    job["finished_at"] = _now()


@app.post("/api/prewarm")
def prewarm(req: Optional[PrewarmReq] = Body(default=None), wait: bool = Query(False, description="block until finished (can take minutes)")) -> JSONResponse:
    req = req or PrewarmReq()
    with _jobs_lock:
        running = next((j for j in _jobs.values() if j["status"] in ("queued", "running")), None)
        if running:
            return JSONResponse({**running, "note": "a prewarm job is already running"}, status_code=202)
        job_id = uuid.uuid4().hex[:8]
        _jobs[job_id] = {"job_id": job_id, "status": "queued", "started_at": _now(), "total": 0, "done": 0, "results": {}}
    th = threading.Thread(target=_run_prewarm, args=(job_id, req), daemon=True, name=f"prewarm-{job_id}")
    th.start()
    if wait:
        th.join()
        return JSONResponse(_jobs[job_id])
    return JSONResponse(_jobs[job_id], status_code=202)


@app.get("/api/prewarm/{job_id}")
def prewarm_status(job_id: str) -> dict:
    if job_id not in _jobs:
        raise HTTPException(404, "unknown job")
    return _jobs[job_id]


@app.get("/api/fallback-bundle")
def fallback_bundle() -> FileResponse:
    if not BUNDLE_PATH.exists():
        raise HTTPException(404, "No bundle yet – POST /api/prewarm first")
    return FileResponse(BUNDLE_PATH, media_type="application/json")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=False)
