"""FINN Push engine: classify → suppress noise → enrich (Results / Management-change cards) → rank → feed.

Cost design: Stage-1 regex rules ($0) resolve routine paperwork; only the remainder goes to ONE batched fast-tier
LLM call per ≤15 filings. Every verdict/card is persisted by filing id in data/push.db, so a filing is never paid for twice.
Degrades without an LLM key: rule-based category guess + deterministic one-liners (flagged classified_by="rule").
CLI:  python push_engine.py [--window 24] [--price 5] [--vol 2] [SYMBOL ...]
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import math
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Literal, Optional

import requests
from pydantic import BaseModel, Field

import data_fetcher as df
import llm_router as llm
from schemas import (CATEGORY_BASE_SIGNAL, DEFAULT_PORTFOLIO, AlertThresholds, Category as C, EODAlert, FilingItem,
                     ManagementChangeBio, ResultsQuarter, ResultsSnapshot)

log = logging.getLogger("finn.push")
PUSH_DB = str(df.DATA_DIR / "push.db")
_dblock = threading.Lock()


# ───────────────────────── persistent KV (classification / cards / pdf text) ─────────────────────────
def _kv():
    c = sqlite3.connect(PUSH_DB, timeout=10, check_same_thread=False)
    c.execute("CREATE TABLE IF NOT EXISTS kv(ns TEXT, k TEXT, v TEXT, ts REAL, PRIMARY KEY(ns,k))")
    return c


def kv_get(ns: str, k: str):
    with _dblock, _kv() as c:
        r = c.execute("SELECT v FROM kv WHERE ns=? AND k=?", (ns, k)).fetchone()
    return json.loads(r[0]) if r else None


def kv_put(ns: str, k: str, v) -> None:
    with _dblock, _kv() as c:
        c.execute("INSERT OR REPLACE INTO kv VALUES(?,?,?,?)", (ns, k, json.dumps(v, default=str), time.time()))


# ═════════════════════════ STAGE 1 — deterministic rules ($0) ═════════════════════════
def _re(p: str) -> re.Pattern:
    return re.compile(p, re.I)


# anything that must never be suppressed by a noise/clean rule (goes to Stage 2)
CORE_MATERIAL = _re(r"financial results?|unaudited|audited (?:standalone|consolidated|financial)|results for the|"
                    r"acqui(?:sition|re)|merger|amalgamation|demerger|joint venture|resign|cessation|appointment of (?:the )?(?:chief|managing|whole|ceo|cfo|coo|cto)|"
                    r"fund[- ]?raising|\bqip\b|rights issue|preferential|buy-?back|penalty|insolvency|\bnclt\b|show cause|"
                    r"capital expenditure|\bcapex\b|new (?:plant|facility)")

NOISE_RULES: list[tuple[re.Pattern, C, int, str]] = [
    (_re(r"trading window"), C.unclassified, 1, "trading window"),
    (_re(r"regulation\s*74\s*\(?5\)?|reg\.?\s*74"), C.unclassified, 1, "Reg 74(5) certificate"),
    (_re(r"\besops?\b|employee stock option|stock option scheme|esos\b|\bsar\b scheme"), C.unclassified, 1, "ESOP"),
    (_re(r"newspaper (?:publication|advertisement)|published in (?:the )?newspapers?"), C.unclassified, 1, "newspaper ad"),
    (_re(r"loss of share certificate|duplicate share certificate|issue of duplicate"), C.unclassified, 1, "duplicate certificate"),
    (_re(r"regulation\s*40\s*\(?9\)?|regulation\s*13\s*\(?3\)?|investor complaints|compliance certificate|"
         r"secretarial compliance|structured digital database|\bsdd\b|shareholding pattern|reg(?:ulation)?\.?\s*24a|certificate under"),
     C.unclassified, 1, "compliance certificate"),
    (_re(r"monitoring agency|statement of deviation|deviation\(s\) or variation"), C.unclassified, 2, "use-of-proceeds report"),
    (_re(r"reminder letter|dematerili[sz]e|unclaimed|\biepf\b|investor education and protection"), C.unclassified, 1, "shareholder reminder"),
    (_re(r"\bbrsr\b|business responsibility|\besg\b|sustainability report"), C.unclassified, 1, "ESG/BRSR"),
    (_re(r"(?:appointment|re-?appointment|resignation) of (?:the )?(?:statutory|secretarial|internal|cost) auditors?"), C.unclassified, 2, "auditor change"),
    (_re(r"(?:intimation|schedule|notice)[^.]{0,60}(?:analyst|investor|institutional)[^.]{0,30}(?:meet|call|conference)|"
         r"analyst[^.]{0,20}/?\s*institutional investor meet[^.]{0,40}schedule"), C.investor_meetings, 2, "investor-meet schedule"),
]
CLEAN_RULES: list[tuple[re.Pattern, C, str]] = [
    (_re(r"postal ballot|e-?voting results|scrutini[sz]er"), C.postal_ballot, "postal ballot"),
    (_re(r"bonus (?:issue|shares?)|stock split|sub-?division of (?:equity )?shares"), C.bonus_split, "bonus/split"),
    (_re(r"annual general meeting|extra-?ordinary general meeting|\bagm\b|\begm\b|book closure"), C.agm_egm, "AGM/EGM"),
    (_re(r"\bdividend\b"), C.dividend, "dividend"),
]
_PLEDGE = _re(r"pledge|encumbrance|invocation of")
_RATING = _re(r"credit rating|crisil|\bicra\b|care ratings|india ratings|ind-?ra|brickwork|acuite|rating (?:agency|action)")
_INSIDER = _re(r"insider trading|regulation\s*7\s*\(?2\)?|\bpit\b regulations|trading by (?:designated|promoter)")
_CALL = _re(r"analyst|investor meet|con\.? call|earnings call|conference call")
_TRANSCRIPT = _re(r"transcript|audio (?:recording|link)|recording of|call recording|webcast")


def det_takeaway(it: FilingItem, words: int = 18) -> str:
    """Zero-cost one-liner: dividend amount if present else first sentence of the filing text."""
    t = f"{it.subject}. {it.body}" if it.body else it.subject
    m = re.search(r"dividend[^.]{0,60}?(?:rs\.?|₹|inr)\s*([\d.,]+)\s*(?:/-)?\s*(?:per|/)\s*(?:equity )?share", t, re.I)
    if m:
        return f"Dividend of ₹{m.group(1).rstrip('.,')} per share declared."
    raw = re.sub(r"^.{0,80}?has (?:informed|intimated) the (?:exchange|stock exchanges?)\s*(?:about|regarding|that|on|of)?\s*", "",
                 (it.body or it.subject).strip(), flags=re.I).strip()
    prot = re.sub(r"\b(?i:Mr|Ms|Mrs|Dr|Shri|Smt|Prof|Rs|No|w\.e\.f|approx)\.|\b[A-Z]\.", lambda m: m.group(0).replace(".", "§"), raw or it.subject)
    s = re.split(r"(?<=[.;])\s", prot)[0].replace("§", ".")
    s = s[:1].upper() + s[1:]
    w = s.split()
    return " ".join(w[:words]) + ("…" if len(w) > words else "")


def stage1(it: FilingItem) -> Optional[tuple[C, int, Optional[str]]]:
    """(category, score, one_liner|None) when rules are confident; None ⇒ ambiguous → LLM."""
    subj, body = it.subject, it.body
    head = f"{subj} {body[:500]}"
    text = f"{subj} {body}"
    # earnings-call vs investor-meet paperwork
    if _CALL.search(subj):
        if re.search(r"schedule|intimation|invite|will be held|to be held|meet(?:ing)?s? with", head, re.I) and not _TRANSCRIPT.search(head):
            return C.investor_meetings, 2, None
        if _TRANSCRIPT.search(head):
            return C.earnings_call, 4, None
        if re.search(r"earnings|conference call|con\.? call", head, re.I):
            return C.earnings_call, 3, None
    if re.search(r"newspaper (?:publication|advertisement)|published in (?:the )?newspapers?", head, re.I):
        # an ad that results were published duplicates the results filing itself → collapse (category kept per ENAM glossary)
        return (C.results if re.search(r"results", head, re.I) else C.unclassified), 2, None
    core = bool(CORE_MATERIAL.search(head))
    if not core:
        for pat, cat, score, _tag in NOISE_RULES:
            if pat.search(head):
                return cat, score, None
        if _PLEDGE.search(head):
            return C.pledging, 6, det_takeaway(it)
        if _INSIDER.search(head):
            return C.insider_trading, 3, None
        if _RATING.search(text) or subj.lower().startswith("credit rating"):
            up = re.search(r"upgrad|downgrad|withdraw|negative|watch", text, re.I)
            return C.credit_rating, 7 if up else 4, det_takeaway(it)
        for pat, cat, _tag in CLEAN_RULES:
            if pat.search(head):
                score = {C.dividend: 5, C.bonus_split: 5, C.agm_egm: 3, C.postal_ballot: 3}[cat]
                return cat, score, det_takeaway(it) if score >= 4 else None
    return None


# Deterministic best-guess category: LLM-outage fallback + cheap prefilter for 30D backfill
_GUESS: list[tuple[re.Pattern, C]] = [
    (_re(r"financial results?|results for the (?:quarter|year|half)|unaudited (?:standalone|consolidated|financial)|audited (?:standalone|consolidated)"), C.results),
    (_re(r"resign|cessation|appointment of|re-?appointment|change in director|key managerial|chief (?:financial|executive|operating)|managing director|\bceo\b|\bcfo\b"), C.management_change),
    (_re(r"acqui(?:sition|re)|merger|amalgamation|demerger|joint venture|subsidiary|stake"), C.ma),
    (_re(r"fund[- ]?raising|\bqip\b|rights issue|preferential|buy-?back|debenture|commercial paper|raising of funds"), C.fund_raise),
    (_re(r"penalty|insolvency|\bnclt\b|show cause|court|tribunal|\bsebi\b order|tax (?:demand|authority)|regulatory (?:order|observation)|litigation"), C.litigation),
    (_re(r"capital expenditure|\bcapex\b|new (?:plant|facility)|capacity expansion|manufacturing facility"), C.capex),
]


def fallback_score(it: FilingItem, cat: C) -> int:
    """Baseline score with the obvious downgrades an LLM would make (used only when the LLM is unavailable)."""
    t = f"{it.subject} {it.body[:500]}"
    if cat is C.management_change:
        if _NONEXEC.search(t):
            return 3
        if re.search(r"re-?appointment", t, re.I):
            return 4 if is_senior(t) else 3
        return 8 if is_senior(t) else 5
    if cat is C.results and re.search(r"intimation|schedule|to consider", it.subject, re.I):
        return 3
    return CATEGORY_BASE_SIGNAL[cat]


def guess_category(it: FilingItem) -> C:
    t = f"{it.subject} {it.body[:600]}"
    for pat, cat in _GUESS:
        if pat.search(t):
            return cat
    return C.press_release


# ═════════════════════════ STAGE 2 — batched fast-tier LLM ═════════════════════════
class LLMItemOut(BaseModel):  # plain BaseModel: Gemini's response_schema rejects additionalProperties
    idx: int
    category: str
    signal_score: int
    is_noise: bool
    takeaway: str


class LLMBatchOut(BaseModel):
    items: list[LLMItemOut]


BANNED = re.compile(r"\b(delve[sd]?|testament|underscor\w*|robust|strategic milestone|landscape|pivotal|synerg\w*|game[- ]chang\w*|"
                    r"paradigm|holistic|cutting[- ]edge|leverag\w*|navigat\w*)\b", re.I)
CLASSIFY_SYS = f"""You triage Indian stock-exchange filings for a lean public-markets fund. Output JSON only.
For each numbered filing return: idx, category, signal_score (1-10), is_noise, takeaway.
category MUST be exactly one of: {", ".join(c.value for c in C)}.
Definitions: results=quarterly/annual results, results board-meeting outcome, shareholder letters; M&A=acquisition/merger/demerger/JV/stake in or sale of a subsidiary;
fund_raise=QIP/rights/preferential/buyback/NCD/CP; management_change=CEO/CFO/MD/director/KMP joining, resigning or retiring; litigation=suits, regulatory orders, fines, insolvency;
capex=approved spend on plant/capacity; press_release=order wins, products, partnerships, clarifications; unclassified=routine paperwork.
Scoring: 9-10 results outcome or transformative M&A; 8-9 CEO/CFO/MD change, material litigation; 6-8 fund_raise, capex, large order win; 4-5 dividend, minor press release;
1-3 routine (independent-director re-appointment, certificates, meeting notices). is_noise=true iff score<4. Board-meeting INTIMATIONS to consider results score 3-4.
takeaway: ONE line, MAX 18 words, only facts present in the text (amounts, names, dates, actions). NEVER invent numbers. If the text has no specifics, say plainly what was filed.
Forbidden words: delve, testament, underscores, robust, strategic milestone, landscape, pivotal, leverage. No "The company". For noise items takeaway may be ''."""


def _llm_classify(chunk: list[FilingItem]) -> list[Optional[LLMItemOut]]:
    prompt = "\n".join(f"[{i}] {it.company or it.symbol} | {it.subject} | {it.body[:350]}" for i, it in enumerate(chunk))
    out = llm.complete(task="push_classify", tier="fast", system=CLASSIFY_SYS, prompt=prompt, schema=LLMBatchOut,
                       ttl_s=None, max_output_tokens=2500)  # persistence handled per-filing in kv
    by = {o.idx: o for o in out.items}
    return [by.get(i) for i in range(len(chunk))]


def _apply(it: FilingItem, cat: C, score: int, line: Optional[str], by: str) -> None:
    it.category, it.signal_score, it.one_liner, it.classified_by = cat, max(1, min(10, score)), line, by  # type: ignore[assignment]
    it.finalize()


def _valid_line(line: str) -> bool:
    return bool(line) and len(line.split()) <= 18 and not BANNED.search(line)


def classify_filings(items: list[FilingItem], use_llm: bool = True) -> dict:
    """Mutates items in place. Returns routing stats for the build log."""
    st = {"total": len(items), "rule": 0, "llm": 0, "cached": 0, "seed": 0, "llm_failed": 0}
    pending: list[FilingItem] = []
    for it in items:
        if it.source == "fallback_seed":
            it.one_liner = it.one_liner or det_takeaway(it)
            st["seed"] += 1
            continue
        if (c := kv_get("cls", it.id)):
            _apply(it, C(c["cat"]), c["score"], c["line"], c["by"])
            st["cached"] += 1
            continue
        if (r := stage1(it)):
            _apply(it, r[0], r[1], r[2], "rule")
            kv_put("cls", it.id, {"cat": it.category.value, "score": it.signal_score, "line": it.one_liner, "by": "rule"})
            st["rule"] += 1
        else:
            pending.append(it)
    for i in range(0, len(pending), 15):
        chunk = pending[i:i + 15]
        try:
            if not use_llm:
                raise RuntimeError("llm disabled")
            outs = _llm_classify(chunk)
        except Exception as e:  # noqa: BLE001
            log.warning("LLM classify unavailable (%s) – rule-based fallback for %d filings", str(e)[:80], len(chunk))
            outs, st["llm_failed"] = [None] * len(chunk), st["llm_failed"] + len(chunk)
        for it, o in zip(chunk, outs):
            if o is None:  # fallback: keyword guess at the category's baseline; not cached so a later run can upgrade it
                g = guess_category(it)
                _apply(it, g, fallback_score(it, g), det_takeaway(it) if fallback_score(it, g) >= 4 else None, "rule")
                continue
            try:
                cat = C(o.category)
            except ValueError:
                cat = guess_category(it)
            base = CATEGORY_BASE_SIGNAL[cat]
            score = max(max(1, base - 6), min(min(10, base + 3), o.signal_score))  # clamp LLM drift around the category prior
            if o.is_noise:
                score = min(score, 3)
            line = o.takeaway.strip() if _valid_line(o.takeaway.strip()) else det_takeaway(it)
            _apply(it, cat, score, line if score >= 4 else None, "llm")
            kv_put("cls", it.id, {"cat": cat.value, "score": it.signal_score, "line": it.one_liner, "by": "llm"})
            st["llm"] += 1
    return st


# ═════════════════════════ Results first-cut card ═════════════════════════
_MON = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_NOT_OUTCOME = _re(r"^(?:prior )?intimation|board meeting intimation|notice of (?:the )?board meeting|schedule of")


def is_results_outcome(it: FilingItem) -> bool:
    if it.category is not C.results:
        return False
    if _NOT_OUTCOME.search(it.subject) or re.search(r"intimation[^.]{0,40}board meeting", it.subject, re.I):
        return False
    return bool(re.search(r"outcome|unaudited|audited|financial results|approved", f"{it.subject} {it.body[:400]}", re.I))


def _period_label(it: FilingItem) -> str:
    """Quarter column the filing refers to, e.g. 'Sep 2026'. Parses 'quarter ended <date>' else infers from filing date."""
    m = re.search(r"ended\s+(?:on\s+)?(?:the\s+)?(?:\d{1,2}(?:st|nd|rd|th)?[\s,.-]+)?([A-Za-z]{3,9})[\s,.-]+(?:\d{1,2}(?:st|nd|rd|th)?[\s,.-]+)?(\d{4})",
                  f"{it.subject} {it.body}", re.I)
    if m and m.group(1)[:3].lower() in ("mar", "jun", "sep", "dec"):
        return f"{m.group(1)[:3].title()} {m.group(2)}"
    d = it.filed_at
    return (f"Sep {d.year}" if d.month >= 10 else f"Dec {d.year - 1}" if d.month <= 3 else f"Mar {d.year}" if d.month <= 6 else f"Jun {d.year}")


def _pct(cur: Optional[float], base: Optional[float]) -> Optional[float]:
    if cur is None or base is None or base <= 0:  # change vs a non-positive base is not meaningful
        return None
    return round((cur / base - 1) * 100, 1)


def _margin(q: ResultsQuarter) -> Optional[float]:
    return round(q.ebitda / q.revenue * 100, 1) if q.ebitda is not None and q.revenue else None


def _bp(a: Optional[float], b: Optional[float]) -> Optional[int]:
    return None if a is None or b is None else round((a - b) * 100)


def _margin_line(label: str, cur: ResultsQuarter, prev: Optional[ResultsQuarter], ya: Optional[ResultsQuarter],
                 qoq: dict, yoy: dict) -> str:
    m = _margin(cur)
    if m is None:
        return ""
    parts = [f"{label} margin {m:.1f}%"]
    ch = [f"{b:+d}bp {n}" for b, n in ((_bp(m, _margin(prev)) if prev else None, "QoQ"), (_bp(m, _margin(ya)) if ya else None, "YoY")) if b is not None]
    s = parts[0] + (f" ({', '.join(ch)})" if ch else "")
    if yoy.get("pat") is not None:
        s += f"; PAT {yoy['pat']:+.1f}% YoY"
    elif qoq.get("pat") is not None:
        s += f"; PAT {qoq['pat']:+.1f}% QoQ"
    return s


def _screener_quarter(sd, label: str) -> ResultsQuarter:
    q = sd.quarters
    row = lambda *n: df._row(q, *n)  # noqa: E731
    sales, op = row("Sales", "Revenue"), row("Operating Profit", "Financing Profit")
    pbt, pat = row("Profit before tax"), row("Net Profit")
    g = lambda r: r.get(label)  # noqa: E731
    return ResultsQuarter(revenue=g(sales), ebitda=g(op), pbt=g(pbt), pat=g(pat))


def _pdf_text(url: str, max_pages: int = 5, max_chars: int = 14000) -> str:
    if (c := kv_get("pdf", url)) is not None:
        return c
    try:
        from pypdf import PdfReader

        r = requests.get(url, headers={"User-Agent": df.UA, "Referer": "https://www.nseindia.com/"}, timeout=30)
        r.raise_for_status()
        txt = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(r.content)).pages[:max_pages])[:max_chars]
    except Exception as e:  # noqa: BLE001
        log.warning("pdf read failed %s: %s", url, e)
        txt = ""
    kv_put("pdf", url, txt)
    return txt


class _Cols(BaseModel):
    revenue: Optional[float] = None
    other_income: Optional[float] = None
    finance_costs: Optional[float] = None
    depreciation: Optional[float] = None
    pbt: Optional[float] = None
    pat: Optional[float] = None


class _PdfResults(BaseModel):
    unit: str  # crore | lakh | million | thousand | rupees
    current: _Cols
    prev_quarter: Optional[_Cols] = None
    year_ago_quarter: Optional[_Cols] = None


_UNIT = {"crore": 1.0, "crores": 1.0, "cr": 1.0, "lakh": 0.01, "lakhs": 0.01, "million": 0.1, "millions": 0.1, "thousand": 1e-4, "thousands": 1e-4, "rupees": 1e-7}


def _results_from_pdf(it: FilingItem) -> Optional[tuple[ResultsQuarter, Optional[ResultsQuarter], Optional[ResultsQuarter]]]:
    """Fallback when Screener hasn't published the quarter yet. LLM only EXTRACTS line items; EBITDA is derived in Python."""
    if not it.attachment_url or not it.attachment_url.lower().endswith(".pdf"):
        return None
    txt = _pdf_text(it.attachment_url)
    if len(txt) < 500:
        return None
    sys_p = ("Extract the standalone-or-consolidated (prefer CONSOLIDATED) quarterly P&L line items from this Indian results filing. "
             "Copy numbers exactly as printed, same unit for all (state the unit: crore|lakh|million|thousand|rupees). "
             "Columns: current quarter, immediately preceding quarter, same quarter previous year. revenue = revenue from operations. "
             "Use null when not shown. Do NOT compute anything.")
    out = llm.complete(task="push_results_extract", tier="fast", system=sys_p, prompt=txt, schema=_PdfResults, ttl_s=None)
    f = _UNIT.get(out.unit.strip().lower())
    if f is None:
        return None

    def conv(c: Optional[_Cols]) -> Optional[ResultsQuarter]:
        if c is None:
            return None
        sc = lambda v: None if v is None else round(v * f, 2)  # noqa: E731
        ebitda = None
        if None not in (c.pbt, c.finance_costs, c.depreciation):
            ebitda = round((c.pbt + c.finance_costs + c.depreciation - (c.other_income or 0)) * f, 2)  # type: ignore[operator]
        return ResultsQuarter(revenue=sc(c.revenue), ebitda=ebitda, pbt=sc(c.pbt), pat=sc(c.pat))

    return conv(out.current), conv(out.prev_quarter), conv(out.year_ago_quarter)  # type: ignore[return-value]


def extract_results_card(it: FilingItem, sd=None) -> ResultsSnapshot:
    """Revenue / EBITDA / PBT / PAT (₹ Cr) with QoQ & YoY. Ground truth = Screener #quarters (itself the audited filing
    extract, $0, deterministic). Only when Screener lags the filing do we pay for a PDF LLM extraction (line items only;
    EBITDA and all % are computed in Python). Re-extracting what Screener already holds would add cost but no information."""
    try:
        sd = sd or df.fetch_screener(it.symbol)
    except Exception as e:  # noqa: BLE001
        sd, note = None, f"Screener unavailable: {e}"
    else:
        note = ""
    label = _period_label(it)
    snap = ResultsSnapshot(source="unavailable", current=ResultsQuarter(), period_label=label, notes=[note] if note else [])
    if sd is not None:
        fin = bool(sd.derived and sd.derived.is_financial)
        cols = [p for p in next(iter(sd.quarters.values()), {}) if p.upper() != "TTM"]
        use = label if label in cols else (cols[-1] if cols else None)
        if use:
            mon, yr = use.split()
            cur = _screener_quarter(sd, use)
            idx = cols.index(use)
            prev = _screener_quarter(sd, cols[idx - 1]) if idx > 0 else None
            ya_label = f"{mon} {int(yr) - 1}"
            ya = _screener_quarter(sd, ya_label) if ya_label in cols else None
            if use == label:
                src_ok = True
            else:
                src_ok = False
                snap.notes.append(f"Screener has not yet published {label}; latest available is {use}.")
            if src_ok or not it.attachment_url:
                return _finish(snap, "screener_quarters", sd.basis, fin, use, cur, prev, ya, src_ok)
        else:
            snap.notes.append("Screener has no quarterly table.")
        fallback_ok = not fin
    else:
        fallback_ok = True
    # Screener lagging → try the filing PDF
    if fallback_ok:
        try:
            r = _results_from_pdf(it)
        except Exception as e:  # noqa: BLE001
            r = None
            snap.notes.append(f"PDF extraction unavailable ({str(e)[:60]})")
        if r and r[0].revenue is not None:
            snap.notes.append("Extracted from the filing PDF; not yet cross-checkable against Screener.")
            return _finish(snap, "filing_pdf_llm", sd.basis if sd else "consolidated", False, label, r[0], r[1], r[2], True)
    if sd is not None and cols:  # Screener lags & no PDF route: show its latest quarter, clearly flagged period_match=False
        i = len(cols) - 1
        mon, yr = cols[i].split()
        ya_l = f"{mon} {int(yr) - 1}"
        return _finish(snap, "screener_quarters", sd.basis, bool(sd.derived and sd.derived.is_financial), cols[i],
                       _screener_quarter(sd, cols[i]), _screener_quarter(sd, cols[i - 1]) if i else None,
                       _screener_quarter(sd, ya_l) if ya_l in cols else None, False)
    return snap


def _finish(snap, source, basis, fin, label, cur, prev, ya, matched) -> ResultsSnapshot:
    qoq = {k: _pct(getattr(cur, k), getattr(prev, k) if prev else None) for k in ("revenue", "ebitda", "pbt", "pat")}
    yoy = {k: _pct(getattr(cur, k), getattr(ya, k) if ya else None) for k in ("revenue", "ebitda", "pbt", "pat")}
    lab = "Financing profit" if fin else "EBITDA"
    snap.source, snap.basis, snap.period_label, snap.period_match = source, basis, label, matched
    snap.current, snap.prev_qtr, snap.year_ago, snap.qoq_pct, snap.yoy_pct = cur, prev, ya, qoq, yoy
    snap.ebitda_label, snap.margin_takeaway = lab, _margin_line(lab, cur, prev, ya, qoq, yoy)
    return snap


# ═════════════════════════ Management-change executive enricher ═════════════════════════
SENIOR = _re(r"chief|\bceo\b|\bcfo\b|\bcoo\b|\bcto\b|\bcio\b|managing director|\bmd\b|chairman|whole[- ]time|executive director|president|head of")
_NONEXEC = _re(r"non[-\s]*executive|independent|nominee|company secretary|compliance officer")


def is_senior(text: str) -> bool:
    return bool(SENIOR.search(text)) and not _NONEXEC.search(text)


NO_PROFILE = "No verified LinkedIn profile found"
_TITLE = r"(?:Mr|Ms|Mrs|Dr|Shri|Smt|Prof|Capt)\.?"
_NAME = r"([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,3})"


class _MgmtExtract(BaseModel):
    incoming_name: Optional[str] = None
    incoming_role: Optional[str] = None
    outgoing_name: Optional[str] = None
    outgoing_role: Optional[str] = None
    effective_date: Optional[str] = None


class _Bullet(BaseModel):
    text: str
    src: int


class _Bullets(BaseModel):
    bullets: list[_Bullet]


def _regex_mgmt(text: str) -> _MgmtExtract:
    out = _MgmtExtract()
    t = re.sub(r"\s+", " ", text)
    m = re.search(rf"(?i:(?:re-?)?appointment of)\s+(?:{_TITLE}\s+)?{_NAME}\s+(?i:as)\s+(?i:the\s+)?([^,;(]+?)(?:\s+(?i:of the company|with effect|w\.e\.f|effective|from|of)\b|[,;(]|\.\s|$)", t)
    if m:
        out.incoming_name, out.incoming_role = m.group(1).strip(), m.group(2).strip()
    m = re.search(rf"(?i:resignation|cessation|retirement) (?i:of)\s+(?:{_TITLE}\s+)?{_NAME}\s+(?i:as|from)\s+(?i:the\s+)?([^,;(]+?)(?:\s+(?i:of the company|with effect|w\.e\.f|effective|from)\b|[,;(]|\.\s|$)", t)
    if m:
        out.outgoing_name, out.outgoing_role = m.group(1).strip(), m.group(2).strip()
    def clean(r):  # "post of CFO", "Chairman Consequent to the above" → "CFO", "Chairman"
        if not r:
            return r
        r = re.sub(r"^(?:the\s+)?(?:post|position|office) of\s+", "", r, flags=re.I)
        r = re.split(r"\s+(?:consequent|pursuant|subject to|as per|The Company)\b", r, flags=re.I)[0]
        return " ".join(r.split()[:5]).strip(" -")
    out.incoming_role, out.outgoing_role = clean(out.incoming_role), clean(out.outgoing_role)
    d = re.search(r"(?:with effect from|w\.e\.f\.?|effective(?: from)?)\s+(?:the\s+)?(?:close of (?:business )?hours? on\s+)?([A-Za-z0-9 ,.-]{6,28}?\d{4})", t, re.I)
    out.effective_date = d.group(1).strip() if d else None
    return out


def _extract_mgmt(it: FilingItem) -> _MgmtExtract:
    pdf = _pdf_text(it.attachment_url, max_pages=2, max_chars=3500) if it.attachment_url and it.attachment_url.lower().endswith(".pdf") else ""
    text = f"{it.subject}. {it.body}\n{pdf}"
    try:
        return llm.complete(task="push_mgmt_extract", tier="fast", ttl_s=None, schema=_MgmtExtract, prompt=text[:5000],
                            system="Extract management-change facts from this Indian stock-exchange filing. incoming_* = person being appointed/re-appointed; "
                                   "outgoing_* = person resigning/retiring/ceasing. Copy names/roles exactly as written; null if absent; never guess.")
    except Exception as e:  # noqa: BLE001
        log.info("mgmt extract via regex (%s)", str(e)[:60])
        return _regex_mgmt(text)


def _short_company(company: str) -> str:
    c = re.sub(r"\((?:india|i)\)|\b(?:limited|ltd|pvt|private|corporation|company|the)\b\.?", " ", company, flags=re.I)
    return " ".join(c.split()) or company


def _name_tokens(name: str) -> list[str]:
    """Meaningful name tokens: drops titles and single-letter initials ('V. Srinivasa Rangan' → srinivasa, rangan)."""
    return [t.lower() for t in re.findall(r"[A-Za-z]{2,}", re.sub(rf"\b{_TITLE}", " ", name)) if len(t) > 1]


def _ddg_hits(name: str, company: str, role: str) -> list[dict]:
    """Two-query public-web lookup (broad → role-scoped). A hit must (a) contain every name token and
    (b) mention the company's key word OR the role, so namesakes at other firms are rejected."""
    try:
        from ddgs import DDGS
    except ImportError:
        return []
    co = _short_company(company)
    qs = [f"{name} {co} linkedin", f"{name} {role} {co}"]
    toks = _name_tokens(name)
    co_tok = (re.findall(r"[A-Za-z]{3,}", co) or [""])[0].lower()
    role_toks = [t.lower() for t in re.findall(r"[A-Za-z]{4,}", role) if t.lower() not in {"director", "officer", "whole", "time", "executive", "chief"}] + \
                [t.lower() for t in re.findall(r"chairman|managing|financial|operating|technology|executive", role, re.I)]
    hits, seen = [], set()
    for q in qs:
        res = None
        for be in ("auto", "brave", "duckduckgo"):  # backends rate-limit independently
            try:
                res = DDGS().text(q, max_results=8, backend=be)
                if res:
                    break
            except Exception as e:  # noqa: BLE001
                log.info("ddg miss %r [%s]: %s", q, be, str(e)[:60])
        if not res:  # all backends throttled → one patient retry
            time.sleep(2.5)
            try:
                res = DDGS().text(q, max_results=8, backend="auto")
            except Exception:  # noqa: BLE001
                res = None
        for r in res or []:
            blob = f"{r.get('title', '')} {r.get('body', '')}".lower()
            url = r.get("href", "")
            if url in seen or not toks or not all(t in blob for t in toks):
                continue
            if not ((co_tok and co_tok in blob) or any(t in blob for t in role_toks)):
                continue  # namesake guard
            seen.add(url)
            hits.append({"url": url, "title": r.get("title", ""), "body": r.get("body", "")})
        if len(hits) >= 3 and any("linkedin.com/in" in h["url"] for h in hits):
            break  # enough evidence; skip the second query
        time.sleep(0.4)
    return hits


def _grounded(text: str, corpus: str) -> bool:
    """Anti-hallucination: every capitalised word ≥4 chars in a bullet must occur in the source corpus."""
    c = corpus.lower()
    body = text.split(" ", 1)[1] if " " in text else ""  # sentence-initial capital is not a proper noun
    return all(w.lower() in c for w in re.findall(r"\b[A-Z][a-zA-Z]{3,}\b", body) if w.lower() not in {"filing", "this", "role", "per", "linkedin", "ceo"})


_WHY = [(_re(r"chief financial|\bcfo\b"), "Owns reporting, capital allocation and investor communication."),
        (_re(r"chief executive|\bceo\b|managing director|\bmd\b|chairman"), "Top-level change; sets strategy and execution priorities."),
        (_re(r"chief operating|\bcoo\b|chief technology|\bcto\b"), "Senior operating change; watch execution continuity."),
        (_re(r"."), "Senior leadership change; watch for continuity.")]


def enrich_management_change(it: FilingItem) -> ManagementChangeBio:
    cached = kv_get("mgmt", it.id)
    if cached:
        return ManagementChangeBio(**cached)
    ex = _extract_mgmt(it)
    incoming = bool(ex.incoming_name)
    person, role = (ex.incoming_name, ex.incoming_role) if incoming else (ex.outgoing_name, ex.outgoing_role)
    text = f"{it.subject} {it.body}"
    action = ("appointed" if incoming else "retired" if re.search(r"retire", text, re.I) else "resigned" if person else "other")
    bio = ManagementChangeBio(person=person, role=role, action=action, effective_date=ex.effective_date, note="")  # type: ignore[arg-type]
    facts = f"Filing: {person or 'Person not named in text'} {action} as {role or 'role not stated'}" + (f", effective {ex.effective_date}" if ex.effective_date else "")
    if ex.outgoing_name and incoming:
        facts += f"; succeeds {ex.outgoing_name}" + (f" ({ex.outgoing_role})" if ex.outgoing_role else "")
    bio.bullets = [facts + "."]
    if not (incoming and person and role and is_senior(role)):
        bio.note = NO_PROFILE if incoming else "Departure – no incoming executive to profile" if person else NO_PROFILE
        if incoming and not is_senior(role or ""):
            bio.note = "Non-executive/board appointment – profile lookup skipped"
        if person:
            kv_put("mgmt", it.id, bio.model_dump())
        return bio
    miss = kv_get("mgmt_miss_v2", it.id)
    if miss and time.time() - miss["t"] < 6 * 3600:  # don't hammer a blocked/empty search backend
        bio.note = NO_PROFILE
        return bio
    hits = _ddg_hits(person, it.company or it.symbol.removesuffix(".NS"), role)
    if not hits:
        kv_put("mgmt_miss_v2", it.id, {"t": time.time()})  # transient: never cache a miss as a verdict
        bio.note = NO_PROFILE
        return bio
    corpus = "\n".join(f"{h['title']} {h['body']}" for h in hits) + "\n" + text
    bullets: list[str] = []
    evidence = 0
    try:
        snip = "\n".join(f"[{i}] {h['url']} :: {h['title']} :: {h['body'][:300]}" for i, h in enumerate(hits[:6]))
        out = llm.complete(task="push_bio", tier="fast", ttl_s=None, schema=_Bullets, max_output_tokens=600,
                           prompt=f"FILING: {facts}\nSNIPPETS:\n{snip}",
                           system="Write up to 3 bullets (≤22 words each) about the INCOMING executive: (1) prior company & role, (2) pedigree/track record, "
                                  "(3) why it matters for the company given the role. Use ONLY facts stated in the SNIPPETS or FILING. src = snippet index "
                                  "(or -1 when the fact comes from the FILING). If a fact is not stated, omit that bullet. No adjectives like 'renowned'.")
        for b in out.bullets[:3]:
            if _grounded(b.text, corpus):
                bullets.append(b.text.strip())
                evidence += 1 if b.src >= 0 else 0
    except Exception as e:  # noqa: BLE001
        log.info("bio LLM unavailable (%s) – deterministic bullets", str(e)[:60])
        li = next((h for h in hits if "linkedin.com/in" in h["url"]), None)
        if li:
            headline = re.split(r"\.\.\.|…|\|", li["title"])[0].strip()
            toks = _name_tokens(person)
            if " - " in headline and all(t in headline.split(" - ", 1)[0].lower() for t in toks):  # "Name - Role - Company"
                bullets.append(f"Per LinkedIn: {headline.split(' - ', 1)[1]}"[:140])
                evidence += 1
            elif all(t in li["body"].lower() for t in toks):
                bullets.append("Per LinkedIn: " + re.split(r"(?<=[.!?])\s", li["body"].strip())[0][:130])
                evidence += 1
        other = next((h for h in hits if h is not li and person.split()[-1].lower() in h["body"].lower()), None)
        if other:
            bullets.append(re.split(r"(?<=[.!?])\s", other["body"].strip())[0][:200])
            evidence += 1
        bullets.append(next(w for p, w in _WHY if p.search(role)))  # role-level context, not company-specific claim
    if evidence >= 1:
        bio.bullets, bio.verified = bullets[:3], True
        li_url = next((h["url"] for h in hits if "linkedin.com/in" in h["url"]), None)
        bio.profile_url = li_url or hits[0]["url"]
        bio.note = "" if li_url else "Bio from public web sources; no LinkedIn profile matched"
        kv_put("mgmt", it.id, bio.model_dump())
    else:
        bio.note = NO_PROFILE
    return bio


# ═════════════════════════ Unified feed ═════════════════════════
class FeedItem(BaseModel):
    kind: Literal["alert", "filing"]
    urgency: float
    symbol: str
    headline: str
    alert: Optional[EODAlert] = None
    filing: Optional[FilingItem] = None
    backfilled: bool = False  # surfaced from the 30D lookback so rich cards are always demonstrable
    demo: bool = False  # synthetic seed – badge in UI


class NoiseDrawer(BaseModel):
    count: int
    by_category: dict[str, int]
    by_symbol: dict[str, int]
    samples: list[dict]  # id, symbol, subject, filed_at – drawer shows these on expand


class PushFeed(BaseModel):
    generated_at: datetime
    window_hours: int
    thresholds: AlertThresholds
    items: list[FeedItem]
    alerts: list[EODAlert]
    collapsed_noise: NoiseDrawer
    coverage_notes: list[str]
    stats: dict


def _urgency_filing(it: FilingItem, now: datetime, window_h: float, alert_syms: set[str]) -> float:
    age_h = max(0.0, (now - it.filed_at).total_seconds() / 3600)
    recency = 10 * max(0.0, 1 - age_h / max(window_h, 1))
    return round(it.signal_score * 10 + recency + (8 if it.symbol in alert_syms else 0), 1)


def _urgency_alert(a: EODAlert) -> float:
    return round(min(100.0, 40 + 4 * abs(a.price_move_pct) + 6 * max(a.volume_ratio - 1, 0)), 1)


def _enrich(items: list[FilingItem], max_results: int = 6, max_mgmt: int = 4) -> None:
    nr = nm = 0
    for it in sorted(items, key=lambda x: -x.signal_score):
        if it.is_noise or it.source == "fallback_seed":
            continue
        try:
            if is_results_outcome(it) and nr < max_results:
                it.enrichment = {"results": extract_results_card(it).model_dump()}
                nr += 1
            elif it.category is C.management_change and nm < max_mgmt:
                it.enrichment = {"management": enrich_management_change(it).model_dump()}
                nm += 1
        except Exception as e:  # noqa: BLE001
            log.warning("enrich failed %s: %s", it.id, e)


def build_push_feed(window_hours: int = 24, price_thresh_pct: float = 5.0, vol_spike_mult: float = 2.0,
                    symbols: Optional[list[str]] = None, near_52w_pct: float = 0.0, min_rich: int = 2,
                    backfill_days: int = 30, demo_fallback: bool = True, use_llm: bool = True) -> PushFeed:
    t0, now = time.time(), datetime.now(df.IST)
    syms = symbols or [s.symbol for s in DEFAULT_PORTFOLIO]
    stats0 = llm.get_routing_stats()
    th = AlertThresholds(price_move_pct=price_thresh_pct, volume_ratio=vol_spike_mult, near_52w_pct=near_52w_pct)
    notes: list[str] = []

    # 1) EOD anomalies
    alerts = [a for s in df.get_eod_batch(syms) if (a := EODAlert.evaluate(s, th))]
    alert_syms = {a.symbol for a in alerts}

    # 2) window filings
    days = max(1, math.ceil(window_hours / 24))
    allf, warns, sources = df.fetch_filings_batch(syms, days=days)
    cutoff = now - timedelta(hours=window_hours)
    win = [f for f in allf if f.filed_at >= cutoff]
    notes += warns[:3]
    if any(v == "fallback_seed" for v in sources.values()):
        notes.append("NSE unreachable for some tickers: showing DEMO seed data (badged).")
    cstats = classify_filings(win, use_llm)

    # 3) rich-card guarantee: ≥min_rich results + management_change, else look back 30D for REAL filings
    backfilled: set[str] = set()
    demo_ids: set[str] = set()
    for cat in (C.results, C.management_change):
        have = [f for f in win if f.category is cat and not f.is_noise]
        need = min_rich - len(have)
        if need <= 0:
            continue
        look, _w, _s = df.fetch_filings_batch(syms, days=backfill_days)
        have_ids = {f.id for f in win}
        cands = [f for f in look if f.id not in have_ids and f.source != "fallback_seed" and guess_category(f) is cat
                 and not (cat is C.results and not is_results_outcome_text(f))]
        classify_filings(cands, use_llm)
        cands = [f for f in cands if f.category is cat and not f.is_noise]
        if cat is C.management_change:  # senior roles first
            cands.sort(key=lambda f: (not is_senior(f"{f.subject} {f.body}"), -f.filed_at.timestamp()))
        else:
            cands.sort(key=lambda f: -f.filed_at.timestamp())
        pick, seen_sym = [], {f.symbol for f in have}
        for f in cands:  # spread across companies
            if len(pick) >= need:
                break
            if f.symbol not in seen_sym:
                pick.append(f)
                seen_sym.add(f.symbol)
        for f in pick:
            win.append(f)
            backfilled.add(f.id)
        if len(have) + len(pick) < min_rich:
            notes.append(f"Only {len(have) + len(pick)} real {cat.value} filing(s) in the last {backfill_days}D across the portfolio.")
        if len(have) + len(pick) < 1 and demo_fallback:
            from fallback_data import seed_filings

            for s in syms:
                if demo_ids:
                    break
                for f in seed_filings(df.bare_symbol(s), hours=48):
                    if f.category is cat:
                        classify_filings([f])
                        win.append(f)
                        demo_ids.add(f.id)
                        break
            if demo_ids:
                notes.append(f"No real {cat.value} filings in {backfill_days}D: DEMO sample added (badged).")

    # 4) enrich & rank
    material = [f for f in win if not f.is_noise]
    noise = [f for f in win if f.is_noise]
    _enrich(material)
    items: list[FeedItem] = []
    for a in alerts:
        items.append(FeedItem(kind="alert", urgency=_urgency_alert(a), symbol=a.symbol, headline=a.headline, alert=a))
    for f in material:
        age_win = window_hours if f.id not in backfilled else backfill_days * 24
        items.append(FeedItem(kind="filing", urgency=_urgency_filing(f, now, age_win, alert_syms), symbol=f.symbol,
                              headline=f.one_liner or f.subject, filing=f, backfilled=f.id in backfilled, demo=f.source == "fallback_seed"))
    items.sort(key=lambda x: -x.urgency)

    drawer = NoiseDrawer(count=len(noise),
                         by_category={k: sum(1 for n in noise if n.category.value == k) for k in {n.category.value for n in noise}},
                         by_symbol={k: sum(1 for n in noise if n.symbol == k) for k in {n.symbol for n in noise}},
                         samples=[{"id": n.id, "symbol": n.symbol, "subject": n.subject, "filed_at": n.filed_at.isoformat(), "url": n.attachment_url}
                                  for n in sorted(noise, key=lambda x: -x.filed_at.timestamp())[:25]])
    s1 = llm.get_routing_stats()
    stats = {"filings_in_window": len(win) - len(backfilled) - len(demo_ids), "classified": cstats, "nse_sources": sources,
             "llm_calls": (s1["live_calls"] - s1["failed_calls"]) - (stats0["live_calls"] - stats0["failed_calls"]), "llm_cost_usd": round(s1["total_cost_usd"] - stats0["total_cost_usd"], 5),
             "noise_suppressed": len(noise), "latency_s": round(time.time() - t0, 1)}
    return PushFeed(generated_at=now, window_hours=window_hours, thresholds=th, items=items, alerts=alerts,
                    collapsed_noise=drawer, coverage_notes=notes, stats=stats)


def is_results_outcome_text(f: FilingItem) -> bool:
    return not (_NOT_OUTCOME.search(f.subject) or re.search(r"intimation[^.]{0,40}board meeting", f.subject, re.I))


# ═════════════════════════ CLI pretty-printer ═════════════════════════
def _fmt(v: Optional[float]) -> str:
    return "  n/a" if v is None else f"{v:,.0f}"


def _pc(v: Optional[float]) -> str:
    return "  n/m" if v is None else f"{v:+.1f}%"


def render(feed: PushFeed) -> str:
    L = [f"FINN PUSH · last {feed.window_hours}h · {len(feed.items)} items · {feed.collapsed_noise.count} noise collapsed · "
         f"alerts@±{feed.thresholds.price_move_pct}%/{feed.thresholds.volume_ratio}×vol"]
    for i in feed.items:
        if i.kind == "alert":
            L.append(f"\n⚡ [{i.urgency:5.1f}] ALERT {i.headline}  ({','.join(i.alert.triggers)})")
            continue
        f = i.filing
        tag = ("DEMO " if i.demo else "") + ("30D-BACKFILL " if i.backfilled else "")
        L.append(f"\n▪ [{i.urgency:5.1f}] {f.symbol.removesuffix('.NS'):9s} {f.category.value:<18s} s={f.signal_score} {tag}({f.classified_by}) {f.filed_at:%d %b %H:%M}")
        L.append(f"    {i.headline}")
        if f.enrichment and "results" in f.enrichment:
            r = ResultsSnapshot(**f.enrichment["results"])
            L.append(f"    RESULTS {r.period_label} [{r.source}/{r.basis}] ₹Cr        cur    QoQ     YoY")
            for k, n in (("revenue", "Revenue"), ("ebitda", r.ebitda_label), ("pbt", "PBT"), ("pat", "PAT")):
                L.append(f"      {n:<17s}{_fmt(getattr(r.current, k)):>10s} {_pc(r.qoq_pct.get(k)):>8s} {_pc(r.yoy_pct.get(k)):>8s}")
            L.append(f"      ↳ {r.margin_takeaway}")
            L += [f"      ! {n}" for n in r.notes]
        if f.enrichment and "management" in f.enrichment:
            m = ManagementChangeBio(**f.enrichment["management"])
            L.append(f"    MGMT {m.action}: {m.person} — {m.role} (eff. {m.effective_date}) verified={m.verified}")
            L += [f"      • {b}" for b in m.bullets]
            if m.note:
                L.append(f"      ↳ {m.note}")
    nz = feed.collapsed_noise
    L.append(f"\n▾ {nz.count} routine filings collapsed: " + ", ".join(f"{k}×{v}" for k, v in sorted(nz.by_category.items(), key=lambda x: -x[1])))
    L += [f"ⓘ {n}" for n in feed.coverage_notes]
    L.append(f"stats: {json.dumps(feed.stats)}")
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="*")
    ap.add_argument("--window", type=int, default=24)
    ap.add_argument("--price", type=float, default=5.0)
    ap.add_argument("--vol", type=float, default=2.0)
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    print(render(build_push_feed(a.window, a.price, a.vol, symbols=a.symbols or None, use_llm=not a.no_llm)))
