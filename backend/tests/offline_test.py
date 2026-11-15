"""Offline test for pull_engine + main (no network, no API keys).

⚠ The fixtures are SYNTHETIC (named "<TICKER> (SYNTHETIC FIXTURE)"): they exercise the code paths – concall mode, no-concall pivot,
low cash-conversion flag, hallucination validators, LLM outage, cache, API – not real company numbers.
Live test on a machine with network + an LLM key:   python pull_engine.py DIXON --refresh   (see README block at the end of the reply)

run:  cd backend && python tests/offline_test.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import types
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="finn_test_"))

from schemas import DocLink, EODSnapshot, ScreenerData, ScreenerDerived  # noqa: E402

# ───────────────────────── stub data_fetcher (only the surface pull_engine/main use) ─────────────────────────
df = types.ModuleType("data_fetcher")
df.DATA_DIR = TMP
df.bare_symbol = lambda s: re.sub(r"\.(NS|BO)$", "", s.strip().upper())


def _row(tbl, *names):  # verbatim behaviour of the real df._row
    for n in names:
        if n in tbl:
            return tbl[n]
        for k in tbl:
            if k.lower().startswith(n.lower()):
                return tbl[k]
    return {}


def _num(txt):
    m = re.search(r"-?\d[\d,]*\.?\d*", txt.replace("%", ""))
    return float(m.group().replace(",", "")) if m else None


df._row, df._num = _row, _num


@dataclass
class ConcallDoc:
    period: str
    url: str
    status: str
    pages: list = field(default_factory=list)
    from_cache: bool = False


CONCALL_PAGES = {
    "Oct 2025": [(3, "Management said: We expect revenue growth of 25% in FY26 and EBITDA margin of 4.5% for the full year. Our mobile segment contributes 70% of revenue."),
                 (7, "Analyst asked about margins. Thank you for the question, we continue to invest in the business.")],
    "Jul 2025": [(4, "We are guiding for revenue growth of 20% over the next two quarters with EBITDA margin of around 4%. Capex of 500 crore is planned for FY26.")],
    "May 2025": [(2, "We expect revenue growth of 18% in FY26 and we aim to reduce debtor days by 10 days over the year.")],
    "Jan 2025": [(5, "Management expects PAT growth of 30% in the coming year and revenue growth of 22% for the next four quarters.")],
}
AR_PAGES = [(12, "Outlook: The company expects to open 150 new stores in FY26 and aims to sustain revenue growth above 20% going forward."),
            (13, "Management discussion: capex of 900 crore is planned for new store openings and supply chain over the next two years.")]


def _ctx(sym, links, max_docs=4, char_budget=60000):
    docs = [ConcallDoc(l.date, l.url, "ok" if l.date in CONCALL_PAGES else "no_text", CONCALL_PAGES.get(l.date, [])) for l in links[:max_docs]]
    blocks = [f"[Concall {d.period}, p.{p}]\n{t}" for d in docs if d.status == "ok" for p, t in d.pages]
    return ("\n\n".join(blocks), docs) if blocks else ("", docs)


def _load(sym, link, **kw):
    if "annual" in link.kind:
        return ConcallDoc(link.title, link.url, "ok", AR_PAGES) if "good-ar" in link.url else ConcallDoc(link.title, link.url, "download_failed", [])
    return ConcallDoc(link.date or link.title, link.url, "ok", CONCALL_PAGES.get(link.date, []))


df.concall_context, df.load_concall = _ctx, _load
df.get_eod_batch = lambda syms: [EODSnapshot(symbol=s, as_of="2026-11-13", close=100.0 + i, prev_close=99.0, return_1d_pct=1.0 + i / 10, volume=1000,
                                             avg_volume_20d=800, volume_ratio=1.25, high_52w=120, low_52w=80, dist_from_52w_high_pct=-5, dist_from_52w_low_pct=20)
                                 for i, s in enumerate(syms)]


# ───────────────────────── synthetic screener fixtures ─────────────────────────
def make(sym, name, *, g, opm0, drift, cc, concalls, ar, ttm=True):
    ann = [f"Mar {y}" for y in range(2021, 2026)]
    sales = [round(10000 * (1 + g) ** i) for i in range(5)]
    opm = [round(opm0 + drift * i, 1) for i in range(5)]
    npf = [round(s * o / 100 * 0.55) for s, o in zip(sales, opm)]
    op = [round(s * o / 100) for s, o in zip(sales, opm)]
    pat3 = sum(npf[-3:])
    cfo = [None, None] + [round(cc * pat3 * w) for w in (0.2, 0.3, 0.5)]
    cfo[0], cfo[1] = 500, 600
    qs = [f"{m} {y}" for y in (2022, 2023, 2024, 2025) for m in ("Mar", "Jun", "Sep", "Dec")][3:15]  # Dec 2022 … Sep 2025
    qsales = [round(2400 * (1 + g / 4) ** i) for i in range(12)]
    qopm = [round(opm0 + drift * i / 4, 1) for i in range(12)]
    qnp = [round(s * o / 100 * 0.55) for s, o in zip(qsales, qopm)]
    S = lambda vals, cols: dict(zip(cols, vals))  # noqa: E731
    pl = {"Sales": {**S(sales, ann), "TTM": qsales[-1] * 4}, "Operating Profit": S(op, ann), "OPM %": S(opm, ann),
          "Net Profit": {**S(npf, ann), "TTM": sum(qnp[-4:])}, "Depreciation": S([300 + 40 * i for i in range(5)], ann)}
    bs = {"Equity Capital": S([100] * 5, ann), "Reserves": S([3000 + 800 * i for i in range(5)], ann), "Borrowings": S([1500 + 100 * i for i in range(5)], ann),
          "Fixed Assets": S([2000 + 300 * i for i in range(5)], ann), "CWIP": S([100, 120, 90, 200, 150], ann)}
    cfd = {"Cash from Operating Activity": S(cfo, ann)}
    rt = {"ROCE %": S([22, 21, 20, 18, 17], ann), "Debtor Days": S([40, 45, 50, 58, 62], ann), "Working Capital Days": S([30, 35, 41, 47, 55], ann)}
    quarters = {"Sales": S(qsales, qs), "Operating Profit": S([round(s * o / 100) for s, o in zip(qsales, qopm)], qs), "OPM %": S(qopm, qs), "Net Profit": S(qnp, qs)}
    cagr = round(((sales[-1] / sales[-4]) ** (1 / 3) - 1) * 100, 1)
    d = ScreenerDerived(cash_conversion_3y=round(sum(cfo[-3:]) / pat3, 2), cash_conversion_periods=ann[-3:], sales_cagr_3y_pct=cagr, sales_cagr_periods=[ann[-4], ann[-1]],
                        latest_opm_pct=qopm[-1], latest_opm_period=qs[-1], debt_to_equity=round(1900 / (100 + 6200), 2), de_period="Mar 2025")
    return ScreenerData(symbol=f"{sym}.NS", basis="consolidated", name=f"{name} (SYNTHETIC FIXTURE)", ratios={"ROCE": "17.0 %", "ROE": "15.2 %"},
                        about=f"{name} manufactures and sells products through its mobile and appliances segments. It operates plants across India.",
                        quarters=quarters, profit_loss=pl, balance_sheet=bs, cash_flow=cfd, ratios_table=rt, derived=d,
                        annual_reports=[DocLink(title="Financial Year 2025", url=f"http://x/{sym}-{'good-ar' if ar else 'bad'}.pdf", date="Financial Year 2025", kind="annual_report")],
                        concalls=[DocLink(title=f"Concall {p}", url=f"http://x/{sym}-{p}.pdf", date=p, kind="concall_transcript") for p in concalls])


FIX = {
    "DIXON": make("DIXON", "Dixon", g=0.28, opm0=3.6, drift=0.15, cc=1.08, concalls=["Oct 2025", "Jul 2025", "May 2025", "Jan 2025"], ar=True),
    "BEL": make("BEL", "BEL", g=0.15, opm0=24, drift=-0.4, cc=0.44, concalls=["Oct 2025", "Jul 2025"], ar=True),
    "TRENT": make("TRENT", "Trent", g=0.30, opm0=12, drift=0.8, cc=0.9, concalls=[], ar=True),
    "NOAR": make("NOAR", "NoAR Co", g=0.10, opm0=10, drift=0, cc=0.8, concalls=[], ar=False),
}
df.fetch_screener = lambda sym, **k: FIX[df.bare_symbol(sym)] if df.bare_symbol(sym) in FIX else (_ for _ in ()).throw(ValueError("no usable tables"))
sys.modules["data_fetcher"] = df

# ───────────────────────── stub llm_router: a "faithful-but-sloppy" model that also hallucinates on purpose ─────────────────────────
llm = types.ModuleType("llm_router")
llm.PROVIDER = "gemini"
llm.model_for = lambda t: {"fast": "stub-flash", "deep": "stub-pro"}[t]
STATE = {"cost": 0.0, "calls": [], "outage": False}


def complete(*, task, prompt, system="", tier="fast", schema=None, ttl_s=None, **kw):
    if STATE["outage"]:
        raise RuntimeError("GEMINI_API_KEY not set")
    STATE["calls"].append((task, tier))
    STATE["cost"] += 0.01 if tier == "deep" else 0.001
    import pull_engine as pe
    if task == "pull_extract":
        g, seen = [], re.finditer(r"^\[((?:Concall|AR) .+?, p\.\d+)\]\n(.*?)(?=\n\n\[|\Z)", prompt, re.M | re.S)
        for m in seen:
            sent = next((s for s in re.split(r"(?<=[.])\s+", m.group(2)) if re.search(r"expect|guiding|aims", s)), None)
            if sent:
                g.append(pe._GuideOut(source_tag=m.group(1), metric="Revenue growth" if "revenue" in sent else "Capex", statement=sent, horizon="FY26", quote=sent))
        g += [pe._GuideOut(source_tag="Concall Oct 2025, p.99", metric="Fake page", statement="Revenue to double by 40%", quote="Revenue to double by forty percent in the coming year"),
              pe._GuideOut(source_tag="Concall Oct 2025, p.3", metric="Fabricated quote", statement="Management promised EBITDA margin of 9%", quote="Management promised that EBITDA margin will reach nine percent by next March")]
        biz = pe._BizOut(what_it_does="Manufactures products via mobile and appliances segments.", segments=["Mobile", "Appliances"],
                         revenue_mix=[pe._SegOut(segment="Mobile", pct=70), pe._SegOut(segment="Ghost", pct=13.37)], source_tag="Concall Oct 2025, p.3")
        if "annual report" in prompt.lower().split("documents")[1][:40]:
            biz.source_tag = "Screener About"
        return pe._ExtractOut(business=biz, guidance=g)
    if task == "pull_synthesis":
        yoy = re.search(r"YoY (\+[\d.]+)%", prompt)
        y = yoy.group(1) if yoy else "+10.0"
        gids = [int(x) for x in re.findall(r"^GID (\d+) \|", prompt, re.M)]
        allowed = re.search(r"ALLOWED_TAGS: (.*)", prompt).group(1)
        page_tag = next((t for t in re.findall(r"\[(?:Concall|AR)[^\]]+\]", allowed)), "[Screener Quarterly P&L]")
        audit = [pe._AuditOut(gid=i, actual_delivered=f"Sales grew {y}% YoY in the latest reported quarter." if i != 1 else "Sales grew 99.9% YoY, far above plan.",
                              verdict=["Beat", "Met", "Missed", "In Progress", "Nonsense"][i % 5]) for i in gids]
        return pe._SynthOut(audit=audit,
                            bull=[pe._PointOut(text=f"Revenue growth of {y}% YoY in the latest quarter", citation="[Screener Quarterly P&L]"),
                                  pe._PointOut(text="Management is very confident about the future of the business", citation=""),  # uncited → dropped
                                  pe._PointOut(text="Order book is strong per the call", citation="[Concall Oct 2025, p.99]"),  # page never supplied → dropped
                                  pe._PointOut(text="Guided revenue growth of 25% for FY26 per management", citation=page_tag)],
                            bear=[pe._PointOut(text="Debtor days lengthened from 40 to 62 over four years", citation="[Screener Ratios]"),
                                  pe._PointOut(text="Margins might compress soon due to competition", citation="")])
    raise AssertionError(task)


llm.complete = complete
llm.get_routing_stats = lambda since_hours=None: {"total_cost_usd": STATE["cost"], "est_cost_saved_by_cache_usd": 0.0, "total_calls": len(STATE["calls"]), "cache_hits": 0,
                                                   "live_calls": len(STATE["calls"]), "failed_calls": 0, "by_model": [], "by_task": []}
sys.modules["llm_router"] = llm

pushs = types.ModuleType("push_engine")


class _Feed:
    def model_dump(self, mode="json"):
        return {"generated_at": "now", "window_hours": 24, "items": [], "alerts": [], "stats": {}}


pushs.build_push_feed = lambda window_hours, price_thresh_pct, vol_spike_mult, symbols=None, near_52w_pct=0.0, use_llm=True: _Feed()
sys.modules["push_engine"] = pushs

import pull_engine as pe  # noqa: E402

# ───────────────────────── tests ─────────────────────────
PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = PASS + bool(cond), FAIL + (not cond)
    print(f"  {'✓' if cond else '✗ FAIL'} {name}{(' — ' + str(extra)) if extra and not cond else ''}")


def all_cites_valid(rep):
    ev = pe.Evidence(pages={}, has_about=True)
    # rebuild page index from guidance citations (the pages that were supplied)
    for g in rep.guidance:
        m = pe._PAGEREF.match(g.citation.strip("[]"))
        ev.pages[(m.group(1).lower(), pe._pn(m.group(2)), int(m.group(3)))] = "x"
    pts = (rep.bull_bear.bull + rep.bull_bear.bear) if rep.bull_bear else []
    return all(pe.valid_tags(p.citation, ev) for p in pts) and all(pe.valid_tags(r.citation, ev) for r in rep.narrative_vs_numbers.rows)


print("\n── unit: validators")
ev = pe.Evidence(pages={("concall", "oct 2025", 3): "We expect revenue growth of 25% in FY26."}, has_about=False)
check("valid concall tag accepted", pe.valid_tags("x [Concall Oct 2025, p.3]", ev) == ["[Concall Oct 2025, p.3]"])
check("unknown page rejected", pe.valid_tags("[Concall Oct 2025, p.4]", ev) == [])
check("screener table tag accepted", pe.valid_tags("[Screener Cash Flow]", ev) == ["[Screener Cash Flow]"])
check("[Screener About] rejected when no about text", pe.valid_tags("[Screener About]", ev) == [])
check("clean_claim drops uncited", pe.clean_claim("Margins are great and improving", "", ev, set()) is None)
check("clean_claim flags ungrounded number", pe.clean_claim("Revenue up 42.7% YoY", "[Screener Cash Flow]", ev, {12.0})[2] is False)
check("quote verbatim ok", pe.quote_in_page("We expect revenue growth of 25% in FY26.", "Hi. We expect revenue growth of 25% in FY26. Thanks"))
check("fabricated quote rejected", not pe.quote_in_page("Revenue will triple by next year for sure", "We expect revenue growth of 25% in FY26."))
check("base_quarter concall Oct-2025 → Sep-2025", pe.base_quarter("concall", "Oct 2025") == (2025, 9))
check("base_quarter concall Jan-2025 → Dec-2024", pe.base_quarter("concall", "Jan 2025") == (2024, 12))
check("base_quarter AR FY2025 → Mar-2025", pe.base_quarter("ar", "FY2025") == (2025, 3))

print("\n── DIXON (concall mode, strong cash conversion)")
r = pe.get_report("DIXON.NS", refresh=True)
check("mode=concall, pages>0", r.mode == "concall" and r.concall_pages > 0, (r.mode, r.concall_pages))
check("4 concall periods", len(r.concall_periods) == 4, r.concall_periods)
check("cash conversion 1.08× → strong, no watch", r.cash_quality.cfo_to_pat_3y_cum == 1.08 and r.cash_quality.verdict == "strong" and not r.cash_quality.watch_flag, r.cash_quality)
check("hallucinated page/quote items dropped", all(g.quote and "nine percent" not in g.quote and "p.99" not in g.citation for g in r.guidance) and any("discarded" in x for x in r.data_gaps), r.data_gaps)
check("guidance items carry verified page cites", len(r.guidance) >= 3 and all(re.match(r"\[Concall .+, p\.\d+\]", g.citation) for g in r.guidance))
check("business revenue_mix keeps stated 70%, drops invented 13.37%", r.business.revenue_mix == {"Mobile": 70.0}, r.business)
n = r.narrative_vs_numbers
check("audit rows present with valid verdicts", n.rows and all(a.verdict in ("Beat", "Met", "Missed", "In Progress") for a in n.rows), n.rows)
check("latest call (Oct 2025, nothing reported since) forced to In Progress", any(a.period == "Oct 2025" and a.verdict == "In Progress" for a in n.rows), [(a.period, a.verdict) for a in n.rows])
check("invalid LLM verdict ('Nonsense') row dropped; invented 99.9% flagged grounded=False", all(a.verdict != "Nonsense" for a in n.rows) and any(not a.grounded for a in n.rows) and any(a.grounded for a in n.rows), [(a.verdict, a.grounded) for a in n.rows])
check("every audit/bull/bear citation resolves", all_cites_valid(r))
check("uncited / bad-page bull+bear dropped and reported", sum("dropped" in x for x in r.data_gaps) >= 2, r.data_gaps)
check("exactly 3 bull and ≤3 bear points (rule top-up marked)", len(r.bull_bear.bull) == 3 and any(p.by == "rule" for p in r.bull_bear.bull + r.bull_bear.bear))
check("deterministic tally summary", "beat" in n.summary and n.credibility_score is not None, n.summary)
check("snapshot: TTM revenue/margin/ROCE/ROE/D/E/CAGR", all(getattr(r.snapshot, k) is not None for k in ("ttm_revenue_cr", "ebitda_margin_pct", "ttm_pat_cr", "roce_pct", "roe_pct", "debt_to_equity", "sales_cagr_3y_pct")), r.snapshot)
check("2 LLM calls: 1 fast + 1 deep", STATE["calls"][-2:] == [("pull_extract", "fast"), ("pull_synthesis", "deep")], STATE["calls"][-2:])
check("cost tracked", r.llm_cost_usd > 0, r.llm_cost_usd)

print("\n── BEL (cash-conversion watch)")
r = pe.get_report("BEL", refresh=True)
cq = r.cash_quality
check("0.44× → weak + watch_flag", cq.cfo_to_pat_3y_cum == 0.44 and cq.verdict == "weak" and cq.watch_flag, cq)
check("commentary names the 0.75× WATCH", "WATCH" in cq.commentary and "0.75" in cq.commentary, cq.commentary)
check("flag list carries it with a citation", any("0.44" in f and "[Screener Cash Flow]" in f for f in r.metrics.flags), r.metrics.flags)
check("working-capital observations from ratios (debtor days lengthened)", any("Debtor days lengthened" in w for w in cq.wc_observations), cq.wc_observations)
check("FCF proxy computed", cq.fcf_3y_sum_cr is not None)
check("bear includes rule-based cash-conversion point", any("0.44" in p.text for p in r.bull_bear.bear), [p.text for p in r.bull_bear.bear])
check("trajectory compressing has ≥1 item", len(r.trajectory.compressing) >= 1, r.trajectory)

print("\n── TRENT (no concalls → Annual Report pivot)")
r = pe.get_report("trent", refresh=True)
check("mode=annual_report, concall_pages=0", r.mode == "annual_report" and r.concall_pages == 0, (r.mode, r.concall_pages))
check("exact no-concall data_gap", pe.NO_CONCALL_GAP in r.data_gaps, r.data_gaps)
check("guidance grounded in AR pages [AR FY2025, p.N]", r.guidance and all(g.source == "annual_report" and re.match(r"\[AR FY2025, p\.\d+\]", g.citation) for g in r.guidance), r.guidance)
check("audit basis=annual_report vs quarterly P&L", r.narrative_vs_numbers.basis == "annual_report" and all("[Screener Quarterly P&L]" in a.citation for a in r.narrative_vs_numbers.rows))
check("no invented concall quotes", not any("Concall" in g.citation for g in r.guidance))
check("AR guidance tested against quarters after Mar-2025 (Jun/Sep 2025 exist → not forced In Progress)", any(a.verdict != "In Progress" for a in r.narrative_vs_numbers.rows), [(a.period, a.verdict) for a in r.narrative_vs_numbers.rows])

print("\n── NOAR (no concall, no readable AR → numbers-only)")
r = pe.get_report("NOAR", refresh=True)
check("mode=financials_only with both gaps", r.mode == "financials_only" and pe.NO_CONCALL_GAP in r.data_gaps and any("numbers-only" in g for g in r.data_gaps), r.data_gaps)
check("no guidance, no audit rows, still has bull/bear + metrics", not r.guidance and not r.narrative_vs_numbers.rows and r.bull_bear.bull and r.metrics.headline)

print("\n── LLM outage (no API key)")
STATE["outage"] = True
r = pe.get_report("DIXON", refresh=True)
check("degraded flag + rule-based bull/bear + gaps explain", r.llm_degraded and all(p.by == "rule" for p in r.bull_bear.bull + r.bull_bear.bear) and any("unavailable" in g for g in r.data_gaps), r.data_gaps)
check("deterministic numbers still complete", r.cash_quality.verdict == "strong" and r.snapshot.ttm_revenue_cr)
STATE["outage"] = False
pe.get_report("DIXON", refresh=True)  # healthy run overwrites the degraded one
nl = pe.get_report("DIXON", refresh=True, use_llm=False)
after = pe.get_report("DIXON")
check("use_llm=false run is returned but never cached over the full report", nl.llm_degraded and after.cache_hit and not after.llm_degraded and after.bull_bear.bull[0].by == "llm", (after.llm_degraded, after.cache_hit))

print("\n── cache + errors")
pe.get_report("DIXON", refresh=True)
calls_before = len(STATE["calls"])
h = pe.get_report("dixon")
check("second call is a cache hit with zero LLM calls", h.cache_hit and len(STATE["calls"]) == calls_before)
cs = pe.cache_stats()
check("cache stats track hits + saved $", cs["hits"] >= 1 and cs["saved_usd"] > 0, cs)
for bad in ("", "BAD TICKER!", "x" * 40):
    try:
        pe.get_report(bad)
        check(f"invalid ticker {bad[:12]!r} → InvalidTicker", False)
    except pe.InvalidTicker:
        check(f"invalid ticker {bad[:12]!r} → InvalidTicker", True)
try:
    pe.get_report("ZZZNOPE", refresh=True)
    check("unknown ticker → NoDataError", False)
except pe.NoDataError:
    check("unknown ticker → NoDataError", True)

print("\n── API (FastAPI TestClient)")
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

c = TestClient(main.app)
check("GET /api/health", c.get("/api/health").json()["status"] == "ok")
p = c.get("/api/portfolio").json()
check("GET /api/portfolio → 16 rows w/ close, 1D %, vol ratio", p["count"] == 16 and all(i["close"] and i["return_1d_pct"] is not None and i["volume_ratio"] for i in p["items"]), p["count"])
check("GET /api/push", c.get("/api/push?window_hours=24&price_thresh=3.0&vol_mult=2.0").status_code == 200)
check("push validation (price_thresh=0 → 422)", c.get("/api/push?price_thresh=0").status_code == 422)
x = c.get("/api/pull/TRENT.NS")
check("GET /api/pull/TRENT.NS (cache hit from earlier run, mode=annual_report)", x.status_code == 200 and x.json()["mode"] == "annual_report" and x.headers["x-finn-cache"] == "hit")
check("GET /api/pull bare lower-case ticker (cache hit)", c.get("/api/pull/trent").headers["x-finn-cache"] == "hit")
check("unknown ticker → 404, invalid → 400", c.get("/api/pull/ZZZNOPE").status_code == 404 and c.get("/api/pull/BAD!!").status_code == 400)
t = c.get("/api/telemetry").json()
check("GET /api/telemetry exposes routing + cache savings", "llm" in t and t["pull_cache"]["hits"] >= 1 and "total_saved_usd" in t, t)
pw = c.post("/api/prewarm?wait=true", json={"tickers": ["DIXON", "BEL", "TRENT", "ZZZNOPE"], "include_push": True}).json()
check("POST /api/prewarm?wait=true done, bad ticker isolated", pw["status"] == "done" and pw["results"]["DIXON"]["ok"] and not pw["results"]["ZZZNOPE"]["ok"], pw)
b = c.get("/api/fallback-bundle").json()
check("fallback_bundle.json: portfolio + push + pulls(DIXON,BEL,TRENT) + errors", b["portfolio"] and b["push"] is not None and {"DIXON", "BEL", "TRENT"} <= set(b["pulls"]) and "ZZZNOPE" in b["errors"], list(b["pulls"]))
pf = c.options("/api/pull/DIXON", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
check("CORS preflight + simple-request header", pf.status_code == 200 and c.get("/api/health", headers={"Origin": "http://x.app"}).headers["access-control-allow-origin"] == "*")

print(f"\n{PASS} passed, {FAIL} failed")
print(f"bundle: {main.BUNDLE_PATH}  ({main.BUNDLE_PATH.stat().st_size // 1024} KB)")
sys.exit(1 if FAIL else 0)
