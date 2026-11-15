"""FINN Pull engine – on-demand company research for ANY NSE ticker, grounded ONLY in primary sources.

Grounding contract (ENAM rubric: "strict grounding on pull")
  • Numbers  : Screener tables (#quarters #profit-loss #balance-sheet #cash-flow #ratios) + ScreenerDerived. 100% Python.
  • Narrative: page-tagged concall transcript pages  [Concall Oct 2025, p.3]  (fallback: Annual Report pages [AR FY2025, p.41]).
  • No news, no broker notes, no model memory. The LLM only (a) EXTRACTS verbatim guidance and (b) matches promises to
    precomputed delivered figures. It never does arithmetic.

Cost / latency design
  fast tier (Flash/mini/Haiku) reads the long transcripts ONCE (~60k chars) → compact guidance digest   [cached 30d – transcripts are immutable]
  deep tier (Pro/4o/Sonnet) sees only the digest + ~2k tokens of deterministic facts → audit verdicts + bull/bear
  whole report cached in SQLite (pull.db) keyed by ticker; degraded (LLM-outage) reports expire in 15 min so they self-heal.

Post-processors (the anti-hallucination layer)
  validate_guidance : cited page must exist in the supplied evidence · quote must appear (fuzzy) on that page · every number in the
                      statement must appear on that page → otherwise the item is dropped.
  clean_claim       : bull/bear/audit text without a resolvable bracketed citation is dropped; unmatched numbers set grounded=False.

CLI:  python pull_engine.py DIXON [--no-llm] [--refresh]
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from pydantic import BaseModel

import data_fetcher as df
import llm_router as llm
from schemas import (BullBear, BusinessSnapshot, CashQualityCheck, CitedPoint, DocLink, FinancialMetrics,
                     FinancialSnapshot, GuidanceItem, MetricPoint, NarrativeAuditRow, NarrativeVsNumbers,
                     PullResearchReport, ScreenerData, ScreenerDerived, Trajectory, TrajectoryItem)

log = logging.getLogger("finn.pull")
PULL_DB = str(df.DATA_DIR / "pull.db")
TTL_S = float(os.getenv("FINN_PULL_TTL_H", "12")) * 3600
DEGRADED_TTL_S = 15 * 60
CASH_CONV_WATCH = 0.75

NO_CONCALL_GAP = "No concall transcripts available on Screener; guidance audit grounded in Annual Report & quarterly filings"
UNREADABLE_CONCALL_GAP = ("Concall transcripts are listed on Screener but none were machine-readable (scanned/failed download); "
                          "guidance audit grounded in Annual Report & quarterly filings")


class PullError(Exception):
    pass


class InvalidTicker(PullError):
    pass


class NoDataError(PullError):
    pass


_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")


def normalize_ticker(t: str) -> str:
    base = df.bare_symbol(t or "")
    if not _TICKER_RE.match(base):
        raise InvalidTicker(f"'{t}' is not a valid NSE symbol")
    return base


# ═════════════════════════ Citation machinery ═════════════════════════
T_Q, T_A, T_BS, T_CF, T_R, T_ABOUT = ("Screener Quarterly P&L", "Screener Annual P&L", "Screener Balance Sheet",
                                      "Screener Cash Flow", "Screener Ratios", "Screener About")
TABLE_TAGS = {T_Q, T_A, T_BS, T_CF, T_R}


def tag(t: str) -> str:
    return f"[{t}]"


_BR = re.compile(r"\[([^\[\]]{2,80})\]")
_PAGEREF = re.compile(r"^(Concall|AR)\s+(.+?),\s*p\.\s*(\d+)$", re.I)
_HDR = re.compile(r"^\[(Concall|AR) (.+?), p\.(\d+)\]\s*$", re.M)


def _pn(s: str) -> str:
    return " ".join(s.lower().split())


@dataclass
class Evidence:
    mode: str = "financials_only"  # concall | annual_report | financials_only
    text: str = ""  # the page-tagged block actually sent to the LLM
    pages: dict = field(default_factory=dict)  # (kind, period_norm, page) → page text
    periods: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    n_pages: int = 0
    has_about: bool = False
    about: str = ""
    gaps: list[str] = field(default_factory=list)


def parse_pages(text: str) -> dict:
    ms = list(_HDR.finditer(text))
    out = {}
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        out[(m.group(1).lower(), _pn(m.group(2)), int(m.group(3)))] = text[m.end():end].strip()
    return out


def valid_tags(text: str, ev: Evidence) -> list[str]:
    """Bracketed tags in `text` that resolve to a table we computed from, or a transcript/AR page we actually supplied."""
    out: list[str] = []
    for inner in _BR.findall(text or ""):
        inner = inner.strip()
        if inner in TABLE_TAGS or (inner == T_ABOUT and ev.has_about):
            out.append(f"[{inner}]")
            continue
        m = _PAGEREF.match(inner)
        if m and (m.group(1).lower(), _pn(m.group(2)), int(m.group(3))) in ev.pages:
            out.append(f"[{m.group(1)} {m.group(2).strip()}, p.{int(m.group(3))}]")
    return list(dict.fromkeys(out))


def strip_brackets(text: str) -> str:
    return " ".join(_BR.sub("", text or "").split())


_YEARISH = re.compile(r"\b(?:FY|Q|H)\s?\d{1,4}\b|\b(?:19|20)\d{2}\b", re.I)
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> set[float]:
    out: set[float] = set()
    for m in _NUM.findall(_YEARISH.sub(" ", text or "")):
        try:
            out.add(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def numbers_grounded(text: str, pool: set[float]) -> bool:
    for n in numbers_in(text):
        if n < 10 and float(n).is_integer():  # "3 quarters", "2 segments" are not data claims
            continue
        tol = 0.51 if float(n).is_integer() else 0.06
        if not any(abs(n - p) <= max(tol, 0.01 * abs(p)) for p in pool):
            return False
    return True


def clean_claim(text: str, citation: str, ev: Evidence, pool: set[float]) -> Optional[tuple[str, str, bool]]:
    """Citation validator for free-text claims → (clean text, canonical citation, grounded) or None (dropped)."""
    tags = valid_tags(f"{citation or ''} {text or ''}", ev)
    body = strip_brackets(text)
    if not tags or len(body.split()) < 3:
        return None
    return body, " ".join(tags), numbers_grounded(body, pool)


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def quote_in_page(quote: str, page: str, thr: float = 0.75) -> bool:
    q, p = _alnum(quote), _alnum(page)
    if len(q) < 20:
        return False
    if q in p:
        return True
    m = difflib.SequenceMatcher(None, q, p, autojunk=False).find_longest_match(0, len(q), 0, len(p))
    return m.size / len(q) >= thr  # tolerates PDF ligature/hyphenation glitches, not paraphrase


# ═════════════════════════ Period helpers ═════════════════════════
_MON = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}


def parse_period(label: str) -> Optional[tuple[int, int]]:
    m = re.search(r"([A-Za-z]{3})[A-Za-z]*[\s'\-]*(\d{4})", label or "")
    if m and m.group(1).lower() in _MON:
        return int(m.group(2)), _MON[m.group(1).lower()]
    return None


def _ord(p: tuple[int, int]) -> int:
    return p[0] * 12 + p[1]


def base_quarter(kind: str, period: str) -> Optional[tuple[int, int]]:
    """Last quarter-end the document already covers: concall in Oct-2025 ⇒ Sep-2025; AR FY2025 ⇒ Mar-2025."""
    if kind == "ar":
        m = re.search(r"(\d{4})", period)
        return (int(m.group(1)), 3) if m else None
    p = parse_period(period)
    if not p:
        return None
    for back in range(1, 13):
        yy, mm = divmod(p[0] * 12 + (p[1] - 1) - back, 12)
        if mm + 1 in (3, 6, 9, 12):
            return yy, mm + 1
    return None


# ═════════════════════════ Evidence (primary-source text) ═════════════════════════
def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception as e:  # noqa: BLE001
        log.warning("%s failed: %s", getattr(fn, "__name__", fn), e)
        return None


_AR_KW = ("outlook", "guidance", "going forward", "expect", "management discussion", "capex", "capital expenditure", "capacity",
          "order book", "growth", "margin", "strategy", "opportunit", "pipeline", "target", "aspir")


def _ar_label(link: DocLink) -> str:
    m = re.search(r"(20\d{2})", f"{link.title} {link.date or ''}")
    return f"FY{m.group(1)}" if m else (link.title or "AR")[:24].strip()


def ar_context(sd: ScreenerData, char_budget: int = 40_000) -> Optional[tuple[str, str, str, int]]:
    """Newest readable Annual Report → MD&A/outlook-weighted, page-tagged excerpt. Returns (text, label, url, n_pages)."""
    for link in sd.annual_reports[:2]:  # Screener lists newest first
        doc = _safe(df.load_concall, sd.symbol, link)  # generic PDF page extractor + SQLite store
        if not doc or doc.status != "ok" or not doc.pages:
            continue
        mdna = next((p for p, t in doc.pages if p >= 3 and "management discussion" in " ".join(t.lower().split())
                     and len(re.findall(r"\.{4,}", t)) < 3), None)  # skip table-of-contents hits

        def score(p: int, t: str) -> float:
            tl = " ".join(t.lower().split())
            s = min(sum(tl.count(k) for k in _AR_KW), 20) + min(tl.count("%") + tl.count("crore"), 10) * 0.4
            return s + (8 if mdna and mdna <= p <= mdna + 18 else 0)

        keep, used = [], 0
        for p, t in sorted(doc.pages, key=lambda x: -score(*x)):
            t = t[:4000]
            if used + len(t) > char_budget and keep:
                continue
            keep.append((p, t))
            used += len(t)
        label = _ar_label(link)
        return "\n\n".join(f"[AR {label}, p.{p}]\n{t}" for p, t in sorted(keep)), label, link.url, len(doc.pages)
    return None


def build_evidence(sd: ScreenerData) -> Evidence:
    ev = Evidence(about=(sd.about or "").strip(), has_about=bool((sd.about or "").strip()))
    docs, ctx = [], ""
    if sd.concalls:
        with ThreadPoolExecutor(max_workers=4) as ex:  # parallel PDF download+extract; concall_context then hits SQLite
            list(ex.map(lambda l: _safe(df.load_concall, sd.symbol, l), sd.concalls[:4]))
        ctx, docs = df.concall_context(sd.symbol, sd.concalls, max_docs=4, char_budget=60_000)
    ok = [d for d in docs if d.status == "ok"]
    n = sum(len(d.pages) for d in ok)  # `concall_pages`
    if n > 0:
        ev.mode, ev.text, ev.periods, ev.sources, ev.n_pages = "concall", ctx, [d.period for d in ok], [d.url for d in ok], n
        if len(ok) < len(docs):
            ev.gaps.append(f"{len(docs) - len(ok)} of the last {len(docs)} concall transcripts were unreadable and skipped.")
    else:  # ── pivot: Annual Report + quarterly filings ──
        ev.gaps.append(NO_CONCALL_GAP if not sd.concalls else UNREADABLE_CONCALL_GAP)
        ar = ar_context(sd)
        if ar:
            ev.mode, ev.text, ev.periods, ev.sources, ev.n_pages = "annual_report", ar[0], [f"AR {ar[1]}"], [ar[2]], ar[3]
        else:
            ev.gaps.append("Annual Report text unavailable (no link, failed download or scanned PDF); analysis is numbers-only.")
    ev.pages = parse_pages(ev.text)
    return ev


# ═════════════════════════ Deterministic financial analysis ═════════════════════════
def _cols(tbl: dict) -> list[str]:
    for r in tbl.values():
        return [p for p in r if p.upper() != "TTM"]
    return []


def _ya(p: str) -> str:
    parts = p.split()
    try:
        return f"{parts[0]} {int(parts[-1]) - 1}"
    except (ValueError, IndexError):
        return ""


def _chg(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return None if a is None or b is None or b <= 0 else round((a / b - 1) * 100, 1)


def _bp(a: Optional[float], b: Optional[float]) -> Optional[int]:
    return None if a is None or b is None else round((a - b) * 100)


def _avg(xs) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _fm(v: Optional[float], d: int = 0) -> str:
    return "n/a" if v is None else f"{v:,.{d}f}"


def _sg(v: Optional[float], suf: str = "%") -> str:
    return "n/a" if v is None else f"{v:+.1f}{suf}"


def _trend(vals: dict, pct: bool) -> str:
    xs = [v for v in vals.values() if v is not None]
    if len(xs) < 3:
        return "n/a"
    last, ref = xs[-1], xs[-3]
    if pct:
        d = last - ref
        return "up" if d >= 0.5 else "down" if d <= -0.5 else "flat"
    if ref == 0:
        return "n/a"
    r = (last - ref) / abs(ref)
    return "up" if r > 0.03 else "down" if r < -0.03 else "flat"


def _cagr3(vals: dict) -> Optional[float]:
    xs = [v for v in vals.values() if v is not None]
    if len(xs) >= 4 and xs[-4] > 0 and xs[-1] > 0:
        return round(((xs[-1] / xs[-4]) ** (1 / 3) - 1) * 100, 1)
    return None


def _mp(label: str, unit: str, vals: dict, cite: str, *, pct: bool = False, cagr: bool = False) -> MetricPoint:
    vals = {k: (None if v is None else round(v, 2)) for k, v in vals.items()}
    return MetricPoint(label=label, unit=unit, values=vals, cagr_3y_pct=_cagr3(vals) if cagr else None,
                       trend=_trend(vals, pct), citation=tag(cite))  # type: ignore[arg-type]


def quarter_facts(sd: ScreenerData, n: int = 8) -> list[dict]:
    q = sd.quarters
    sales, opm, npf = df._row(q, "Sales", "Revenue"), df._row(q, "OPM %", "Financing Margin %"), df._row(q, "Net Profit")
    out = []
    for p in _cols(q)[-n:]:
        ya = _ya(p)
        out.append(dict(period=p, sales=sales.get(p), sales_yoy=_chg(sales.get(p), sales.get(ya)), opm=opm.get(p),
                        opm_yoy_bp=_bp(opm.get(p), opm.get(ya)), np=npf.get(p), np_yoy=_chg(npf.get(p), npf.get(ya))))
    return out


def render_quarters(qf: list[dict]) -> str:
    lines = []
    for f in qf:
        bp = "n/a" if f["opm_yoy_bp"] is None else "%+dbp" % f["opm_yoy_bp"]
        lines.append(f"{f['period']} | Sales {_fm(f['sales'])} (YoY {_sg(f['sales_yoy'])}) | OPM {_fm(f['opm'], 1)}% (YoY {bp}) | "
                     f"NetProfit {_fm(f['np'])} (YoY {_sg(f['np_yoy'])})")
    return "\n".join(lines)


@dataclass
class Analysis:
    metrics: FinancialMetrics
    snapshot: FinancialSnapshot
    cash: CashQualityCheck
    trajectory: Trajectory
    calc: dict
    qfacts: list[dict]
    facts_text: str


def analyze(sd: ScreenerData) -> Analysis:  # noqa: C901 – one linear pipeline, kept in one place on purpose
    d = sd.derived or ScreenerDerived()
    fin = d.is_financial
    pl, q, bs, cf, rt = sd.profit_loss, sd.quarters, sd.balance_sheet, sd.cash_flow, sd.ratios_table
    R = df._row
    acols, qcols = _cols(pl), _cols(q)
    a5 = acols[-5:]
    sales, op, opm, npf, dep = R(pl, "Sales", "Revenue"), R(pl, "Operating Profit", "Financing Profit"), R(pl, "OPM %", "Financing Margin %"), \
        R(pl, "Net Profit"), R(pl, "Depreciation")
    qsales, qop, qnp = R(q, "Sales", "Revenue"), R(q, "Operating Profit", "Financing Profit"), R(q, "Net Profit")
    bor, eq, res, fa, cwip = R(bs, "Borrowings"), R(bs, "Equity Capital"), R(bs, "Reserves"), R(bs, "Fixed Assets"), R(bs, "CWIP")
    cfo, roce_r = R(cf, "Cash from Operating Activity"), R(rt, "ROCE %")
    eb_label = "Financing Profit" if fin else "EBITDA"
    qf = quarter_facts(sd)
    flags: list[str] = []

    def ttm(plrow: dict, qrow: dict) -> Optional[float]:
        v = plrow.get("TTM")
        if v is not None:
            return v
        xs = [qrow.get(p) for p in qcols[-4:]]
        return round(sum(xs), 2) if len(xs) == 4 and all(x is not None for x in xs) else None

    # ── per-year series
    nw = {p: (eq[p] + res[p]) if eq.get(p) is not None and res.get(p) is not None else None for p in _cols(bs)}
    roe = {p: round(npf[p] / nw[p] * 100, 1) for p in a5 if npf.get(p) is not None and nw.get(p) and nw[p] > 0}
    de = {p: round(bor[p] / nw[p], 2) for p in _cols(bs)[-5:] if bor.get(p) is not None and nw.get(p) and nw[p] > 0}
    capex: dict[str, Optional[float]] = {}  # proxy: ΔNet block + ΔCWIP + depreciation (Screener's cash-flow table hides capex)
    bcols = _cols(bs)
    for i, p in enumerate(bcols):
        if i and fa.get(p) is not None and fa.get(bcols[i - 1]) is not None and dep.get(p) is not None:
            c = fa[p] - fa[bcols[i - 1]] + dep[p]
            if cwip and cwip.get(p) is not None and cwip.get(bcols[i - 1]) is not None:
                c += cwip[p] - cwip[bcols[i - 1]]
            capex[p] = round(max(c, 0.0), 1)
    fcf = {p: round(cfo[p] - capex[p], 1) for p in a5 if cfo.get(p) is not None and capex.get(p) is not None}

    sub = lambda r, ps: {p: r.get(p) for p in ps}  # noqa: E731
    headline = [
        _mp(f"Revenue (annual)", "₹ Cr", {**sub(sales, a5), **({"TTM": sales["TTM"]} if sales.get("TTM") is not None else {})}, T_A, cagr=True),
        _mp(f"{eb_label} margin % (annual)", "%", sub(opm, a5), T_A, pct=True),
        _mp("PAT (annual)", "₹ Cr", {**sub(npf, a5), **({"TTM": npf["TTM"]} if npf.get("TTM") is not None else {})}, T_A, cagr=True),
        _mp("Revenue (quarterly)", "₹ Cr", {f["period"]: f["sales"] for f in qf}, T_Q),
        _mp(f"{eb_label} margin % (quarterly)", "%", {f["period"]: f["opm"] for f in qf}, T_Q, pct=True),
        _mp("PAT (quarterly)", "₹ Cr", {f["period"]: f["np"] for f in qf}, T_Q),
    ]
    if not fin and sub(roce_r, a5) and any(v is not None for v in sub(roce_r, a5).values()):
        headline.append(_mp("ROCE %", "%", sub(roce_r, a5), T_R, pct=True))
    if roe:
        headline.append(_mp("ROE % (PAT ÷ closing net worth)", "%", roe, T_A, pct=True))

    bsm: list[MetricPoint] = []
    if bor:
        bsm.append(_mp("Borrowings", "₹ Cr", {p: bor.get(p) for p in bcols[-5:]}, T_BS))
    if de and not fin:
        bsm.append(_mp("Debt / Equity", "×", de, T_BS))
    for lab, names in (("Working-capital days", ("Working Capital Days",)), ("Debtor days", ("Debtor Days",)),
                       ("Inventory days", ("Inventory Days",)), ("Cash-conversion cycle (days)", ("Cash Conversion Cycle",))):
        r = R(rt, *names)
        if r:
            bsm.append(_mp(lab, "days", {p: r.get(p) for p in _cols(rt)[-5:]}, T_R))

    # ── cash quality (deterministic)
    cc, periods = d.cash_conversion_3y, d.cash_conversion_periods
    cfo3 = round(sum(cfo[p] for p in periods), 1) if len(periods) == 3 else None
    pat3 = round(sum(npf[p] for p in periods), 1) if len(periods) == 3 else None
    fcf3 = round(sum(fcf[p] for p in periods), 1) if periods and all(p in fcf for p in periods) else None
    cqm = [_mp("Cash from operations", "₹ Cr", sub(cfo, a5), T_CF), _mp("PAT", "₹ Cr", sub(npf, a5), T_A),
           _mp("CFO ÷ PAT", "×", {p: round(cfo[p] / npf[p], 2) for p in a5 if cfo.get(p) is not None and npf.get(p) and npf[p] > 0}, T_CF)]
    if fcf and not fin:
        cqm.append(_mp("Free cash flow (proxy)", "₹ Cr", fcf, T_CF))
    wc_obs: list[str] = []
    for lab, names in (("Debtor days", ("Debtor Days",)), ("Inventory days", ("Inventory Days",)), ("Working-capital days", ("Working Capital Days",)),
                       ("Cash-conversion cycle", ("Cash Conversion Cycle",))):
        r = R(rt, *names)
        xs = [(p, r[p]) for p in _cols(rt) if r.get(p) is not None]
        if len(xs) >= 4:
            (p0, v0), (p1, v1) = xs[-4], xs[-1]
            if abs(v1 - v0) >= 5:
                wc_obs.append(f"{lab} {'lengthened' if v1 > v0 else 'shortened'} from {v0:.0f} ({p0}) to {v1:.0f} ({p1}) days {tag(T_R)}")
    if fin:
        verdict, comm = "insufficient_data", "Bank/NBFC: CFO and working-capital cycle are not meaningful for lenders; judge on NIM/NPA/CASA from filings."
    elif cc is None:
        verdict, comm = "insufficient_data", "Fewer than 3 overlapping annual periods of CFO and PAT (or cumulative PAT ≤ 0)."
    else:
        verdict = "strong" if cc >= 0.95 else "adequate" if cc >= CASH_CONV_WATCH else "weak"
        comm = (f"{cc:.2f}× of 3Y cumulative PAT converted to operating cash ({periods[0]}–{periods[-1]}: CFO ₹{cfo3:,.0f} Cr vs PAT ₹{pat3:,.0f} Cr)."
                if cfo3 is not None and pat3 is not None else f"{cc:.2f}× 3Y cash conversion.")
        if cc < CASH_CONV_WATCH:
            comm += f" Below {CASH_CONV_WATCH:.2f}× → working-capital / cash-conversion WATCH."
        if fcf3 is not None:
            comm += f" FCF proxy over the same 3Y: ₹{fcf3:,.0f} Cr."
    cash = CashQualityCheck(verdict=verdict, cfo_to_pat_3y_cum=cc, periods=periods, cfo_3y_cr=cfo3, pat_3y_cr=pat3, fcf_3y_sum_cr=fcf3,  # type: ignore[arg-type]
                            watch_flag=bool(cc is not None and cc < CASH_CONV_WATCH and not fin), commentary=comm + f" {tag(T_CF)}",
                            wc_observations=wc_obs, citation=f"{tag(T_CF)} {tag(T_A)}")

    # ── snapshot
    top = lambda k: df._num(sd.ratios.get(k, "")) if sd.ratios.get(k) else None  # noqa: E731
    roce_last = top("ROCE") if top("ROCE") is not None else next((roce_r[p] for p in reversed(a5) if roce_r.get(p) is not None), None)
    roe_last = top("ROE") if top("ROE") is not None else (roe[max(roe, key=lambda p: acols.index(p))] if roe else None)
    t_rev, t_eb, t_pat = ttm(sales, qsales), ttm(op, qop), ttm(npf, qnp)
    snap = FinancialSnapshot(
        as_of_quarter=qcols[-1] if qcols else "", basis=sd.basis, is_financial=fin, ebitda_label=eb_label, ttm_revenue_cr=t_rev, ttm_ebitda_cr=t_eb,
        ebitda_margin_pct=round(t_eb / t_rev * 100, 1) if t_eb is not None and t_rev else d.latest_opm_pct, ttm_pat_cr=t_pat,
        roce_pct=None if fin else roce_last, roe_pct=roe_last, debt_to_equity=None if fin else d.debt_to_equity,
        sales_cagr_3y_pct=d.sales_cagr_3y_pct, notes=list(d.notes), citation=f"{tag(T_Q)} {tag(T_A)} {tag(T_R)}")

    # ── deterministic flags + trajectory + calc for rule-based bull/bear
    last = qf[-1] if qf else {}
    roce_xs = [roce_r[p] for p in a5 if roce_r.get(p) is not None]
    roce_chg = round(roce_xs[-1] - roce_xs[-4], 1) if len(roce_xs) >= 4 else None
    deb = R(rt, "Debtor Days")
    deb_xs = [deb[p] for p in _cols(rt) if deb.get(p) is not None]
    deb_chg = round(deb_xs[-1] - deb_xs[-4], 0) if len(deb_xs) >= 4 else None
    de_xs = [v for v in de.values() if v is not None]
    de_chg = round(de_xs[-1] - de_xs[-4], 2) if len(de_xs) >= 4 else None
    cfo_last = next((cfo[p] for p in reversed(a5) if cfo.get(p) is not None), None)
    calc = dict(cagr=d.sales_cagr_3y_pct, sales_yoy=last.get("sales_yoy"), np_yoy=last.get("np_yoy"), opm_bp=last.get("opm_yoy_bp"), opm=last.get("opm"),
                roce=None if fin else roce_last, roce_chg=None if fin else roce_chg, cc=None if fin else cc, de=None if fin else d.debt_to_equity,
                de_chg=None if fin else de_chg, deb_chg=None if fin else deb_chg, fin=fin, q=last.get("period", ""))
    if not fin and cc is not None and cc < CASH_CONV_WATCH:
        flags.append(f"Cash conversion {cc:.2f}× (3Y CFO ÷ PAT) is below {CASH_CONV_WATCH:.2f}× — working-capital / cash-conversion watch {tag(T_CF)}")
    if not fin and d.debt_to_equity is not None and d.debt_to_equity > 1.5:
        flags.append(f"Debt/Equity {d.debt_to_equity:.2f}× ({d.de_period}) is elevated {tag(T_BS)}")
    if not fin and de_chg is not None and de_chg >= 0.3:
        flags.append(f"Debt/Equity rose {de_chg:+.2f}× over 3 years {tag(T_BS)}")
    if last.get("opm_yoy_bp") is not None and last["opm_yoy_bp"] <= -200:
        flags.append(f"{eb_label} margin compressed {last['opm_yoy_bp']:+d}bp YoY in {last['period']} {tag(T_Q)}")
    if last.get("np_yoy") is not None and last["np_yoy"] < 0:
        flags.append(f"PAT fell {last['np_yoy']:.1f}% YoY in {last['period']} {tag(T_Q)}")
    if not fin and roce_chg is not None and roce_chg <= -3:
        flags.append(f"ROCE down {abs(roce_chg):.1f}pp over 3 years {tag(T_R)}")
    if not fin and deb_chg is not None and deb_chg >= 10:
        flags.append(f"Debtor days up {deb_chg:.0f} over 3 years {tag(T_R)}")
    if not fin and cfo_last is not None and cfo_last < 0:
        flags.append(f"Operating cash flow negative in the latest year {tag(T_CF)}")

    acc: list[TrajectoryItem] = []
    comp: list[TrajectoryItem] = []

    def place(delta: Optional[float], thr: float, label: str, up: str, down: str, cite: str) -> None:
        if delta is None:
            return
        if delta >= thr:
            acc.append(TrajectoryItem(label=label, detail=up, citation=tag(cite)))
        elif delta <= -thr:
            comp.append(TrajectoryItem(label=label, detail=down, citation=tag(cite)))

    r2, p2 = _avg([f["sales_yoy"] for f in qf[-2:]]), _avg([f["sales_yoy"] for f in qf[-4:-2]])
    if r2 is not None and p2 is not None:
        place(r2 - p2, 3, "Revenue growth", f"YoY growth averaged {r2:+.1f}% over the last 2 quarters vs {p2:+.1f}% in the 2 before",
              f"YoY growth slowed to {r2:+.1f}% avg over the last 2 quarters from {p2:+.1f}% in the 2 before", T_Q)
    n2, np2 = _avg([f["np_yoy"] for f in qf[-2:]]), _avg([f["np_yoy"] for f in qf[-4:-2]])
    if n2 is not None and np2 is not None:
        place(n2 - np2, 5, "PAT growth", f"PAT YoY averaged {n2:+.1f}% over the last 2 quarters vs {np2:+.1f}% before",
              f"PAT YoY slowed to {n2:+.1f}% avg over the last 2 quarters from {np2:+.1f}% before", T_Q)
    m2 = _avg([f["opm_yoy_bp"] for f in qf[-2:]])
    if m2 is not None:
        place(m2, 100, f"{eb_label} margin", f"Margin up {m2:.0f}bp YoY on average over the last 2 quarters",
              f"Margin down {abs(m2):.0f}bp YoY on average over the last 2 quarters", T_Q)
    if not fin and roce_chg is not None:
        place(roce_chg, 2, "ROCE", f"ROCE up {roce_chg:.1f}pp over 3 years ({roce_xs[-1]:.1f}%)", f"ROCE down {abs(roce_chg):.1f}pp over 3 years ({roce_xs[-1]:.1f}%)", T_R)
    if not fin and deb_chg is not None:
        place(-deb_chg, 10, "Receivables cycle", f"Debtor days shortened by {abs(deb_chg):.0f} over 3 years", f"Debtor days lengthened by {deb_chg:.0f} over 3 years", T_R)

    facts = "\n".join([
        f"COMPANY: {sd.name or sd.symbol} ({sd.symbol}) | basis: {sd.basis} | financial_company: {fin} | ₹ crore",
        f"QUARTERLY (latest 8) {tag(T_Q)}:", render_quarters(qf),
        f"ANNUAL (last 5) {tag(T_A)}:",
        *[f"{p} | Sales {_fm(sales.get(p))} | {eb_label} margin {_fm(opm.get(p), 1)}% | NetProfit {_fm(npf.get(p))}"
          + (f" | ROCE {_fm(roce_r.get(p), 1)}%" if not fin and roce_r.get(p) is not None else "")
          + "".join(f" | {lab} {_fm(r.get(p))}" for lab, r in (("DebtorDays", R(rt, "Debtor Days")), ("InventoryDays", R(rt, "Inventory Days")),
                                                                 ("WorkingCapitalDays", R(rt, "Working Capital Days"))) if r.get(p) is not None and not fin)
          for p in a5],
        f"3Y sales CAGR: {_sg(d.sales_cagr_3y_pct)} {tag(T_A)}",
        f"CASH & BALANCE SHEET {tag(T_CF)} {tag(T_BS)}: {comm}" + (f" | D/E {d.debt_to_equity:.2f}× ({d.de_period})" if d.debt_to_equity is not None and not fin else ""),
        *[f"FLAG: {f}" for f in flags]])
    return Analysis(FinancialMetrics(headline=headline, balance_sheet=bsm, cash_quality=cqm, flags=flags), snap, cash,
                    Trajectory(accelerating=acc, compressing=comp), calc, qf, facts)


def det_points(c: dict) -> tuple[list[CitedPoint], list[CitedPoint]]:
    """Rule-based bull/bear: the LLM-outage fallback and the top-up when the model returns <3 valid cited points."""
    P = lambda t, cite: CitedPoint(text=t, citation=tag(cite), by="rule")  # noqa: E731
    bull, bear = [], []
    if c["cagr"] is not None and c["cagr"] >= 12:
        bull.append(P(f"3-year sales CAGR of {c['cagr']:.1f}%", T_A))
    if c["opm_bp"] is not None and c["opm_bp"] >= 100:
        bull.append(P(f"Operating margin up {c['opm_bp']:+d}bp YoY in {c['q']}", T_Q))
    if c["np_yoy"] is not None and c["np_yoy"] >= 15:
        bull.append(P(f"PAT grew {c['np_yoy']:+.1f}% YoY in {c['q']}", T_Q))
    if c["roce"] is not None and c["roce"] >= 18:
        bull.append(P(f"ROCE of {c['roce']:.1f}% indicates high capital efficiency", T_R))
    if c["cc"] is not None and c["cc"] >= 0.95:
        bull.append(P(f"Profits convert to cash: 3Y CFO ÷ PAT of {c['cc']:.2f}×", T_CF))
    if c["de"] is not None and c["de"] <= 0.3:
        bull.append(P(f"Low leverage: Debt/Equity {c['de']:.2f}×", T_BS))
    if c["cc"] is not None and c["cc"] < CASH_CONV_WATCH:
        bear.append(P(f"Weak cash conversion: 3Y CFO ÷ PAT of only {c['cc']:.2f}×", T_CF))
    if c["opm_bp"] is not None and c["opm_bp"] <= -100:
        bear.append(P(f"Operating margin down {c['opm_bp']:+d}bp YoY in {c['q']}", T_Q))
    if c["np_yoy"] is not None and c["np_yoy"] < 0:
        bear.append(P(f"PAT fell {c['np_yoy']:.1f}% YoY in {c['q']}", T_Q))
    if c["de"] is not None and c["de"] > 1:
        bear.append(P(f"Leverage: Debt/Equity {c['de']:.2f}×", T_BS))
    if c["roce_chg"] is not None and c["roce_chg"] <= -3:
        bear.append(P(f"ROCE down {abs(c['roce_chg']):.1f}pp over 3 years", T_R))
    if c["deb_chg"] is not None and c["deb_chg"] >= 10:
        bear.append(P(f"Debtor days up {c['deb_chg']:.0f} over 3 years", T_R))
    if c["sales_yoy"] is not None and c["cagr"] is not None and c["sales_yoy"] < c["cagr"] / 2:
        bear.append(P(f"Latest-quarter sales growth {c['sales_yoy']:+.1f}% YoY is under half the 3Y CAGR ({c['cagr']:.1f}%)", T_Q))
    return bull, bear


# ═════════════════════════ LLM stage 1 (fast tier): read the transcripts once ═════════════════════════
class _GuideOut(BaseModel):  # plain BaseModel: Gemini's response_schema rejects additionalProperties
    source_tag: str
    metric: str
    statement: str
    horizon: str = ""
    quote: str = ""


class _SegOut(BaseModel):
    segment: str
    pct: float


class _BizOut(BaseModel):
    what_it_does: str
    segments: list[str] = []
    revenue_mix: list[_SegOut] = []
    source_tag: str = ""


class _ExtractOut(BaseModel):
    business: Optional[_BizOut] = None
    guidance: list[_GuideOut] = []


EXTRACT_SYS = """You extract facts from PRIMARY company documents for an equity research tool. Output JSON only.
HARD RULES: use ONLY the text in ABOUT and DOCUMENTS. No outside knowledge, no news, no estimates, no inference beyond the text.
Every DOCUMENTS page starts with a header like [Concall Oct 2025, p.3] or [AR FY2025, p.41].

guidance = explicit FORWARD-LOOKING commitments by management about revenue/growth, margins, capex, capacity, order book, debt, cash flow, or other measurable targets.
  For each: source_tag = the page header copied EXACTLY without brackets (e.g. "Concall Oct 2025, p.3"); metric (≤5 words); statement (≤30 words, keep every number/unit exactly as written);
  horizon ("FY26", "next 2 quarters", "" if unstated); quote = a VERBATIM excerpt (≤25 words) copied from that page that contains the figure/commitment.
  Skip vague optimism with nothing measurable. Max 3 per document and 12 in total. Prefer the most specific, quantified statements.
business: what_it_does (≤30 words, from ABOUT or the documents); segments (≤6 names as the company names them);
  revenue_mix = [{segment, pct}] ONLY when percentages are explicitly stated; source_tag = a page header (no brackets) or "Screener About".
If a field is not in the text, leave it empty. Never guess."""


def validate_extract(out: _ExtractOut, ev: Evidence, about: str) -> tuple[Optional[BusinessSnapshot], list[GuidanceItem], int]:
    dropped, items, seen, per_doc = 0, [], set(), {}
    for g in out.guidance:
        m = _PAGEREF.match(g.source_tag.strip(" []"))
        key = (m.group(1).lower(), _pn(m.group(2)), int(m.group(3))) if m else None
        page = ev.pages.get(key) if key else None
        if page is None or not g.statement.strip() or not quote_in_page(g.quote, page) or not numbers_grounded(g.statement, numbers_in(page)):
            dropped += 1
            continue
        k2 = (key[0], key[1], _pn(g.metric))
        if k2 in seen or per_doc.get(key[:2], 0) >= 3:
            continue
        seen.add(k2)
        per_doc[key[:2]] = per_doc.get(key[:2], 0) + 1
        period = m.group(2).strip()  # type: ignore[union-attr]
        items.append(GuidanceItem(metric=g.metric.strip(), statement=" ".join(g.statement.split()), horizon=g.horizon.strip(),
                                  quote=" ".join(g.quote.split()), citation=f"[{m.group(1)} {period}, p.{int(m.group(3))}]",  # type: ignore[union-attr]
                                  period=period, source="concall" if key[0] == "concall" else "annual_report"))
    items = items[:12]
    return validate_business(out.business, ev, about), items, dropped


def validate_business(b: Optional[_BizOut], ev: Evidence, about: str) -> Optional[BusinessSnapshot]:
    fallback = None
    if about:  # deterministic fallback: Screener's own company description (primary, company-provided)
        sents = re.split(r"(?<=[.!?])\s+", about.strip())
        fallback = BusinessSnapshot(what_it_does=" ".join(sents[:2])[:320], citation=tag(T_ABOUT))
    if b is None or not b.what_it_does.strip():
        return fallback
    src = b.source_tag.strip(" []")
    m = _PAGEREF.match(src)
    key = (m.group(1).lower(), _pn(m.group(2)), int(m.group(3))) if m else None
    if key and key in ev.pages:
        cite, text = f"[{m.group(1)} {m.group(2).strip()}, p.{int(m.group(3))}]", ev.pages[key]  # type: ignore[union-attr]
    elif about:
        cite, text = tag(T_ABOUT), about
    else:
        return None
    nums = numbers_in(text)
    mix = {s.segment.strip(): round(s.pct, 1) for s in b.revenue_mix
           if s.segment.strip() and (s.pct in nums or round(s.pct) in nums)}  # pct must literally appear in the cited source
    if sum(mix.values()) > 105:
        mix = {}
    return BusinessSnapshot(what_it_does=" ".join(b.what_it_does.split()), segments=[s.strip() for s in b.segments[:6] if s.strip()],
                            revenue_mix=mix, citation=cite)


# ═════════════════════════ LLM stage 2 (deep tier): promises vs delivered ═════════════════════════
class _AuditOut(BaseModel):
    gid: int
    actual_delivered: str
    verdict: str


class _PointOut(BaseModel):
    text: str
    citation: str


class _SynthOut(BaseModel):
    audit: list[_AuditOut] = []
    bull: list[_PointOut] = []
    bear: list[_PointOut] = []


SYNTH_SYS = """You are an equity analyst auditing management's narrative against delivered numbers. Output JSON only.
HARD RULES: use ONLY the FACTS and GUIDANCE below (company-filed primary data). No outside knowledge, news, broker views, or estimates. DO NOT compute anything:
quote the figures exactly as given (YoY %, bp changes and margins are already calculated for you).
1) audit: for EVERY GID listed, return {gid, actual_delivered, verdict}. actual_delivered ≤28 words citing the delivered figures with their period labels, using only the quarters in that
   GID's TESTED list. verdict ∈ "Beat" (clearly above what was guided) | "Met" (in line, roughly within 10% of the guided level) | "Missed" | "In Progress" (horizon not yet complete,
   or the guidance is not testable with the quarters available). Never invent a verdict for an untestable item – use "In Progress".
2) bull: exactly 3 and bear: exactly 3 evidence-backed points, each ≤25 words, each built on specific figures from FACTS or on a GUIDANCE statement.
   Each point needs citation = one or more bracketed tags copied EXACTLY from ALLOWED_TAGS. A point without a valid tag is discarded.
Style: plain, specific, no adjectives like robust/strong/impressive, no hedging boilerplate."""

_VERDICTS = {"beat": "Beat", "met": "Met", "missed": "Missed", "miss": "Missed", "in progress": "In Progress", "in_progress": "In Progress", "inprogress": "In Progress"}


def _tested(g: GuidanceItem, qcols: list[str]) -> list[str]:
    bq = base_quarter("ar" if g.source == "annual_report" else "concall", g.period)
    if not bq:
        return qcols[-4:]
    return [p for p in qcols if (pp := parse_period(p)) and _ord(pp) > _ord(bq)]


def pick_audit_set(guidance: list[GuidanceItem], cap: int = 8) -> list[GuidanceItem]:
    """≤2 quantified items per source document, newest documents first (→ the '4-quarter' audit)."""
    def recency(g: GuidanceItem) -> int:
        bq = base_quarter("ar" if g.source == "annual_report" else "concall", g.period)
        return _ord(bq) if bq else 0
    out, per = [], {}
    for g in sorted(guidance, key=lambda g: (-recency(g), not re.search(r"\d", g.statement))):
        if per.get(g.period, 0) < 2:
            out.append(g)
            per[g.period] = per.get(g.period, 0) + 1
    return out[:cap]


# ═════════════════════════ Orchestrator ═════════════════════════
def _llm_cost() -> float:
    try:
        return float(llm.get_routing_stats()["total_cost_usd"])
    except Exception:  # noqa: BLE001
        return 0.0


def build_pull_report(ticker: str, use_llm: bool = True) -> PullResearchReport:
    t0, c0 = time.perf_counter(), _llm_cost()
    base = normalize_ticker(ticker)
    try:
        sd = df.fetch_screener(base)
    except ValueError as e:
        raise NoDataError(str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise PullError(f"Screener fetch failed for {base}: {e}") from e

    an = analyze(sd)
    ev = build_evidence(sd)
    gaps = list(ev.gaps) + [f"Screener note: {n}" for n in (sd.derived.notes if sd.derived else []) if "CAGR" in n or "Cash conversion" in n]
    degraded = not use_llm
    business, guidance = validate_business(None, ev, ev.about), []
    audit_rows: list[NarrativeAuditRow] = []
    bull: list[CitedPoint] = []
    bear: list[CitedPoint] = []

    if not use_llm:
        gaps.append("LLM stages skipped (use_llm=false): no guidance extraction/audit; bull/bear are rule-based.")
    else:
        # ── stage 1: fast tier reads long primary text once
        if ev.text:
            try:
                prompt = f"ABOUT (Screener, company-provided):\n{ev.about or '(none)'}\n\nDOCUMENTS ({'concall transcripts' if ev.mode == 'concall' else 'annual report excerpt'}):\n{ev.text}"
                out = llm.complete(task="pull_extract", tier="fast", system=EXTRACT_SYS, prompt=prompt, schema=_ExtractOut,
                                   ttl_s=30 * 86400, max_output_tokens=4096)
                business, guidance, dropped = validate_extract(out, ev, ev.about)
                if dropped:
                    gaps.append(f"{dropped} extracted guidance item(s) discarded: cited page/quote/figure could not be verified against the source text.")
                if not guidance:
                    gaps.append("No explicit, verifiable forward guidance found in the available documents.")
            except Exception as e:  # noqa: BLE001
                degraded = True
                gaps.append(f"Guidance extraction unavailable ({str(e)[:80]}); audit skipped.")
        # ── stage 2: deep tier synthesis over a compact digest
        qcols = [f["period"] for f in an.qfacts]
        aset = pick_audit_set(guidance)
        allowed = [tag(t) for t in (T_Q, T_A, T_BS, T_CF, T_R)] + list(dict.fromkeys(g.citation for g in guidance))
        gtxt = "\n".join(f"GID {i} | {g.citation} | {g.metric} | {g.statement} | horizon: {g.horizon or 'unstated'} | TESTED: "
                         f"{', '.join(_tested(g, qcols)) or 'NONE'}" for i, g in enumerate(aset)) or "(no guidance items)"
        try:
            so = llm.complete(task="pull_synthesis", tier="deep", system=SYNTH_SYS, max_output_tokens=3000,
                              prompt=f"ALLOWED_TAGS: {' '.join(allowed)}\n\nFACTS\n{an.facts_text}\n\nGUIDANCE TO AUDIT\n{gtxt}", schema=_SynthOut)
            pool_f = numbers_in(an.facts_text)
            pool_b = pool_f | numbers_in(" ".join(g.statement for g in guidance))
            by_gid = {a.gid: a for a in so.audit}
            for i, g in enumerate(aset):
                tested = _tested(g, qcols)
                a = by_gid.get(i)
                if not tested:  # deterministic guard: nothing reported since the promise ⇒ cannot be judged
                    audit_rows.append(NarrativeAuditRow(metric_or_theme=g.metric, promised_guidance=g.statement + (f" ({g.horizon})" if g.horizon else ""),
                                                        actual_delivered="No quarter reported since this guidance was given.", verdict="In Progress",
                                                        citation=g.citation, period=g.period))
                    continue
                v = _VERDICTS.get((a.verdict if a else "").strip().lower())
                if not a or not v:
                    continue
                body = strip_brackets(a.actual_delivered)
                if len(body.split()) < 3:
                    continue
                audit_rows.append(NarrativeAuditRow(
                    metric_or_theme=g.metric, promised_guidance=g.statement + (f" ({g.horizon})" if g.horizon else ""), actual_delivered=body,
                    verdict=v, citation=f"{g.citation} {tag(T_Q)}", period=g.period, grounded=numbers_grounded(body, pool_f)))  # type: ignore[arg-type]
            for src, dst in ((so.bull, bull), (so.bear, bear)):
                for p in src:
                    c = clean_claim(p.text, p.citation, ev, pool_b)
                    if c:
                        dst.append(CitedPoint(text=c[0], citation=c[1], grounded=c[2], by="llm"))
                if len(dst) < len(src):
                    gaps.append(f"{len(src) - len(dst)} {'bull' if src is so.bull else 'bear'} point(s) dropped: no valid primary-source citation.")
        except Exception as e:  # noqa: BLE001
            degraded = True
            gaps.append(f"Synthesis unavailable ({str(e)[:80]}); bull/bear are rule-based and no guidance audit was produced.")

    # rule-based top-up so the card always has ≤3 cited points per side (marked by="rule")
    rb, rr = det_points(an.calc)
    for have, extra in ((bull, rb), (bear, rr)):
        have_text = {h.text for h in have}
        for p in extra:
            if len(have) >= 3:
                break
            if p.text not in have_text:
                have.append(p)
    bull, bear = bull[:3], bear[:3]

    # ── narrative-vs-numbers tally (deterministic; no LLM "credibility" opinion)
    tally = {k: sum(1 for r in audit_rows if r.verdict == k) for k in ("Beat", "Met", "Missed", "In Progress")}
    decided = tally["Beat"] + tally["Met"] + tally["Missed"]
    where = ("concall" if ev.mode == "concall" else "annual_report") if audit_rows else "none"
    src_lab = ", ".join(ev.periods) if ev.periods else "n/a"
    nvn = NarrativeVsNumbers(
        rows=audit_rows[:8], tally=tally, basis=where,  # type: ignore[arg-type]
        credibility_score=max(1, min(10, round(10 * (tally["Beat"] + tally["Met"]) / decided))) if decided else None,
        summary=(f"{len(audit_rows)} guided items from {src_lab}: {tally['Beat']} beat · {tally['Met']} met · {tally['Missed']} missed · {tally['In Progress']} in progress."
                 if audit_rows else "No verifiable guidance to audit."))

    # ── final citation validation pass over every deterministic + LLM text that carries tags
    flags = [f for f in an.metrics.flags if valid_tags(f, ev)]
    an.metrics.flags = flags
    an.cash.wc_observations = [w for w in an.cash.wc_observations if valid_tags(w, ev)]
    sources = [f"https://www.screener.in/company/{base}/" + ("consolidated/" if sd.basis == "consolidated" else "")] + ev.sources

    return PullResearchReport(
        symbol=f"{base}.NS", company=sd.name or base, basis=sd.basis, mode=ev.mode, concall_pages=ev.n_pages if ev.mode == "concall" else 0,  # type: ignore[arg-type]
        concall_periods=ev.periods if ev.mode == "concall" else [], sources_used=sources, business=business, snapshot=an.snapshot,
        metrics=an.metrics, trajectory=an.trajectory, narrative_vs_numbers=nvn, cash_quality=an.cash, guidance=guidance,
        bull_bear=BullBear(bull=bull, bear=bear), data_gaps=list(dict.fromkeys(gaps)), llm_degraded=degraded,
        llm_cost_usd=round(max(0.0, _llm_cost() - c0), 5), latency_ms=int((time.perf_counter() - t0) * 1000))


# ═════════════════════════ SQLite report cache + telemetry ═════════════════════════
_dblock = threading.Lock()
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _db() -> sqlite3.Connection:
    c = sqlite3.connect(PULL_DB, timeout=10, check_same_thread=False)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS reports(symbol TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at REAL, expires_at REAL, llm_cost REAL, gen_ms INTEGER);
    CREATE TABLE IF NOT EXISTS pull_events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, symbol TEXT, hit INTEGER, saved_usd REAL, saved_ms INTEGER, served_ms INTEGER);
    """)
    return c


def _read(base: str) -> tuple[Optional[PullResearchReport], bool]:
    with _dblock, _db() as c:
        r = c.execute("SELECT payload, expires_at FROM reports WHERE symbol=?", (base,)).fetchone()
    if not r:
        return None, False
    try:
        return PullResearchReport.model_validate_json(r[0]), r[1] > time.time()
    except Exception:  # noqa: BLE001  schema changed since it was cached
        return None, False


def _write(base: str, rep: PullResearchReport) -> None:
    ttl = DEGRADED_TTL_S if rep.llm_degraded else TTL_S
    with _dblock, _db() as c:
        c.execute("INSERT OR REPLACE INTO reports VALUES(?,?,?,?,?,?)",
                  (base, rep.model_dump_json(), time.time(), time.time() + ttl, rep.llm_cost_usd, rep.latency_ms))


def _event(base: str, hit: bool, rep: PullResearchReport, served_ms: int) -> None:
    with _dblock, _db() as c:
        c.execute("INSERT INTO pull_events(ts,symbol,hit,saved_usd,saved_ms,served_ms) VALUES(?,?,?,?,?,?)",
                  (time.time(), base, int(hit), rep.llm_cost_usd if hit else 0.0, max(0, rep.latency_ms - served_ms) if hit else 0, served_ms))


def get_report(ticker: str, refresh: bool = False, use_llm: bool = True) -> PullResearchReport:
    """Cached entry point used by the API. One in-flight generation per ticker; stale-if-error."""
    base, t0 = normalize_ticker(ticker), time.perf_counter()
    ms = lambda: int((time.perf_counter() - t0) * 1000)  # noqa: E731
    if not refresh:
        rep, fresh = _read(base)
        if rep and fresh:
            rep.cache_hit = True
            _event(base, True, rep, ms())
            return rep
    with _locks_guard:
        lock = _locks.setdefault(base, threading.Lock())
    with lock:
        rep, fresh = _read(base)
        if not refresh and rep and fresh:  # another request finished while we waited
            rep.cache_hit = True
            _event(base, True, rep, ms())
            return rep
        try:
            new = build_pull_report(base, use_llm)
        except PullError:
            raise
        except Exception as e:  # noqa: BLE001
            if rep:
                log.warning("pull %s failed (%s) – serving stale cache", base, e)
                rep.cache_hit, rep.data_gaps = True, rep.data_gaps + [f"Served stale cached report; refresh failed: {str(e)[:80]}"]
                return rep
            raise PullError(f"Pull analysis failed for {base}: {e}") from e
        if use_llm:  # an LLM-less run must never overwrite a full report
            _write(base, new)
        _event(base, False, new, ms())
        return new


def cache_stats() -> dict:
    with _dblock, _db() as c:
        c.row_factory = sqlite3.Row
        n = c.execute("SELECT COUNT(*) n FROM reports").fetchone()["n"]
        e = c.execute("SELECT COALESCE(SUM(hit),0) hits, COUNT(*)-COALESCE(SUM(hit),0) misses, COALESCE(SUM(saved_usd),0) usd, COALESCE(SUM(saved_ms),0) ms, "
                      "COALESCE(AVG(CASE WHEN hit=1 THEN served_ms END),0) hit_ms FROM pull_events").fetchone()
        g = c.execute("SELECT COALESCE(AVG(llm_cost),0) cost, COALESCE(AVG(gen_ms),0) ms FROM reports").fetchone()
    tot = e["hits"] + e["misses"]
    return {"reports_cached": n, "hits": e["hits"], "misses": e["misses"], "hit_rate": round(e["hits"] / tot, 3) if tot else 0.0,
            "saved_usd": round(e["usd"], 5), "saved_latency_s": round(e["ms"] / 1000, 1), "avg_hit_ms": round(e["hit_ms"]),
            "avg_generation_cost_usd": round(g["cost"], 5), "avg_generation_ms": round(g["ms"])}


# ═════════════════════════ CLI ═════════════════════════
def render(r: PullResearchReport) -> str:
    L = [f"FINN PULL · {r.symbol} · {r.company} · {r.basis} · mode={r.mode} · concall_pages={r.concall_pages} · cache={r.cache_hit} · "
         f"${r.llm_cost_usd:.4f} · {r.latency_ms}ms{' · LLM-DEGRADED' if r.llm_degraded else ''}"]
    if r.business:
        L.append(f"\nBUSINESS: {r.business.what_it_does} {r.business.citation}\n  segments={r.business.segments} mix={r.business.revenue_mix}")
    s = r.snapshot
    if s:
        L.append(f"\nSNAPSHOT ({s.as_of_quarter}): TTM rev ₹{s.ttm_revenue_cr} Cr | {s.ebitda_label} margin {s.ebitda_margin_pct}% | PAT ₹{s.ttm_pat_cr} Cr | "
                 f"ROCE {s.roce_pct}% | ROE {s.roe_pct}% | D/E {s.debt_to_equity} | 3Y CAGR {s.sales_cagr_3y_pct}%")
    if r.narrative_vs_numbers:
        n = r.narrative_vs_numbers
        L.append(f"\nNARRATIVE vs NUMBERS [{n.basis}] credibility={n.credibility_score} — {n.summary}")
        for a in n.rows:
            L.append(f"  [{a.verdict:<11}] {a.metric_or_theme} ({a.period}){'' if a.grounded else ' ⚠'}\n      promised : {a.promised_guidance}\n      delivered: {a.actual_delivered}\n      {a.citation}")
    if r.cash_quality:
        L.append(f"\nCASH: {r.cash_quality.verdict} cc={r.cash_quality.cfo_to_pat_3y_cum} watch={r.cash_quality.watch_flag} — {r.cash_quality.commentary}")
        L += [f"  · {w}" for w in r.cash_quality.wc_observations]
    if r.trajectory:
        L.append("\nTRAJECTORY ▲ " + " | ".join(f"{i.label}: {i.detail}" for i in r.trajectory.accelerating) + "\n           ▼ " +
                 " | ".join(f"{i.label}: {i.detail}" for i in r.trajectory.compressing))
    if r.guidance:
        L.append("\nGUIDANCE:")
        L += [f"  · {g.metric}: {g.statement} ({g.horizon}) {g.citation}" for g in r.guidance]
    if r.bull_bear:
        L.append("\nBULL:")
        L += [f"  + {p.text} {p.citation}{'' if p.grounded else ' ⚠'} ({p.by})" for p in r.bull_bear.bull]
        L.append("BEAR:")
        L += [f"  − {p.text} {p.citation}{'' if p.grounded else ' ⚠'} ({p.by})" for p in r.bull_bear.bear]
    if r.metrics and r.metrics.flags:
        L += ["\nFLAGS:"] + [f"  ! {f}" for f in r.metrics.flags]
    L += ["\nDATA GAPS:"] + [f"  ⓘ {g}" for g in r.data_gaps]
    return "\n".join(L)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    rep = get_report(a.ticker, refresh=a.refresh, use_llm=not a.no_llm)
    print(rep.model_dump_json(indent=1) if a.json else render(rep))
