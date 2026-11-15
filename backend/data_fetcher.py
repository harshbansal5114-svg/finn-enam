"""FINN data layer: EOD (yfinance) · NSE announcements · Screener.in primary parser · Concall PDF extractor.

Rules: works for ANY NSE ticker (nothing keyed to the demo portfolio except the offline fallback seed);
every fetcher degrades gracefully; all arithmetic is deterministic Python (zero LLM maths).

CLI:  python data_fetcher.py TCS.NS      # smoke test one ticker
      python data_fetcher.py warm        # pre-fetch real filings + screener for the demo portfolio (commit data/cache)
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

from schemas import (CATEGORY_BASE_SIGNAL, DEFAULT_PORTFOLIO, DocLink, EODSnapshot, FilingItem, ScreenerData,
                     ScreenerDerived)

log = logging.getLogger("finn.data")

DATA_DIR = Path(os.getenv("FINN_DATA_DIR", Path(__file__).parent / "data"))
CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CONCALL_DB = str(DATA_DIR / "concalls.db")

try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # tzdata missing on slim images
    IST = timezone(timedelta(hours=5, minutes=30))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")


def bare_symbol(symbol: str) -> str:
    """'DIXON.NS' / 'dixon' / 'DIXON.BO' → 'DIXON' (NSE API + Screener both want the bare symbol)."""
    return re.sub(r"\.(NS|BO)$", "", symbol.strip().upper())


# ───────────────────────── tiny JSON disk cache ─────────────────────────
def _path(key: str) -> Path:
    return CACHE_DIR / f"{hashlib.sha1(key.encode()).hexdigest()[:20]}.json"


def _disk_get(key: str, ttl_s: float):
    p = _path(key)
    if p.exists() and time.time() - p.stat().st_mtime < ttl_s:
        try:
            return json.loads(p.read_text())
        except Exception:
            return None
    return None


def _disk_get_stale(key: str):
    return _disk_get(key, ttl_s=float("inf"))


def _disk_put(key: str, value) -> None:
    _path(key).write_text(json.dumps(value, default=str))


# ═════════════════════════ 1. EOD market data ═════════════════════════
def get_eod_snapshot(symbol: str, ttl_s: int = 900) -> EODSnapshot:
    """Latest close, 1D %, 20D avg volume (excl. latest session), volume ratio, 52W distances."""
    sym = symbol.strip().upper()
    if not sym.endswith((".NS", ".BO")):
        sym += ".NS"
    key = f"eod:{sym}"
    if (c := _disk_get(key, ttl_s)):
        return EODSnapshot(**c)
    try:
        import yfinance as yf

        hist = yf.Ticker(sym).history(period="1y", interval="1d", auto_adjust=False).dropna(subset=["Close"])
        if len(hist) < 22:
            raise ValueError(f"insufficient history ({len(hist)} rows) – bad ticker or recent listing")
        last, prev = hist.iloc[-1], hist.iloc[-2]
        avg20 = float(hist["Volume"].iloc[-21:-1].mean())
        hi, lo, close = float(hist["High"].max()), float(hist["Low"].min()), float(last["Close"])
        snap = EODSnapshot(
            symbol=sym, as_of=hist.index[-1].strftime("%Y-%m-%d"), close=round(close, 2),
            prev_close=round(float(prev["Close"]), 2),
            return_1d_pct=round((close / float(prev["Close"]) - 1) * 100, 2),
            volume=int(last["Volume"]), avg_volume_20d=round(avg20), volume_ratio=round(float(last["Volume"]) / avg20, 2) if avg20 > 0 else 0.0,
            high_52w=round(hi, 2), low_52w=round(lo, 2),
            dist_from_52w_high_pct=round((close / hi - 1) * 100, 2), dist_from_52w_low_pct=round((close / lo - 1) * 100, 2))
        _disk_put(key, snap.model_dump())
        return snap
    except Exception as e:  # noqa: BLE001
        log.warning("EOD failed %s: %s", sym, e)
        if (stale := _disk_get_stale(key)):
            s = EODSnapshot(**stale)
            s.error = f"stale: {e}"
            return s
        return EODSnapshot(symbol=sym, as_of="", close=0, prev_close=0, return_1d_pct=0, volume=0, avg_volume_20d=0,
                           volume_ratio=0, high_52w=0, low_52w=0, dist_from_52w_high_pct=0, dist_from_52w_low_pct=0, error=str(e))


def get_eod_batch(symbols: list[str]) -> list[EODSnapshot]:
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(get_eod_snapshot, symbols))


# ═════════════════════════ 2. NSE corporate announcements ═════════════════════════
class NSEClient:
    """Persistent session. Contract (verified shape of the public JSON endpoint):
       GET /api/corporate-announcements?index=equities&symbol=<BARE>&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY
       NSE returns an empty/HTML body (no error!) when cookies are missing or dates aren't DD-MM-YYYY."""
    HOME = "https://www.nseindia.com"
    API = "https://www.nseindia.com/api/corporate-announcements"
    REFERER = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"

    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA, "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate", "Referer": self.REFERER, "Connection": "keep-alive",
            "X-Requested-With": "XMLHttpRequest"})
        self._warmed_at = 0.0
        self._lock = threading.Lock()

    def _warm(self, force: bool = False) -> None:
        with self._lock:
            if not force and time.time() - self._warmed_at < 240:  # cookies go stale in minutes
                return
            self.s.cookies.clear()
            self.s.get(self.HOME, headers={"Accept": "text/html,application/xhtml+xml"}, timeout=12)
            self.s.get(self.REFERER, headers={"Accept": "text/html"}, timeout=12)
            self._warmed_at = time.time()

    def fetch(self, symbol: str, days: int, retries: int = 3) -> list[dict]:
        end = datetime.now(IST)
        params = {"index": "equities", "symbol": bare_symbol(symbol),
                  "from_date": (end - timedelta(days=days)).strftime("%d-%m-%Y"),  # DD-MM-YYYY, strictly
                  "to_date": end.strftime("%d-%m-%Y")}
        err: Exception | None = None
        for i in range(retries):
            try:
                self._warm(force=i > 0)
                r = self.s.get(self.API, params=params, timeout=15)
                body = r.text.strip()
                if r.status_code == 200 and body[:1] in "[{":
                    j = r.json()
                    return j if isinstance(j, list) else j.get("data", [])
                err = RuntimeError(f"HTTP {r.status_code}, non-JSON body ({len(body)}B) – likely blocked/cookie issue")
            except Exception as e:  # noqa: BLE001
                err = e
            time.sleep(1.2 * (i + 1))
        raise err or RuntimeError("NSE unreachable")


_nse = NSEClient()
_DT_FORMATS = ("%Y-%m-%d %H:%M:%S", "%d-%b-%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%d-%b-%Y", "%Y-%m-%d")


def _parse_ist(*candidates) -> datetime:
    """Parse the first parsable of (an_dt, sort_date, exchdisstime) as Asia/Kolkata-aware datetime."""
    for s in candidates:
        if not s:
            continue
        for fmt in _DT_FORMATS:
            try:
                return datetime.strptime(str(s).strip(), fmt).replace(tzinfo=IST)
            except ValueError:
                continue
    return datetime.now(IST)


def _row_key(r: dict) -> str:
    return str(r.get("seq_id") or f"{r.get('an_dt')}|{r.get('desc')}|{r.get('attchmntFile')}")


def map_nse_row(r: dict, base: str, source: str = "nse") -> FilingItem:
    """Exact NSE keys → FilingItem. desc→subject, attchmntText→body, attchmntFile→attachment_url,
    an_dt (fallback sort_date / exchdisstime)→filed_at (IST), seq_id→seq_id, sm_name→company."""
    subject = (r.get("desc") or "").strip()
    seq = str(r["seq_id"]) if r.get("seq_id") else None
    fid = f"{base}-{seq}" if seq else hashlib.sha1(_row_key(r).encode()).hexdigest()[:16]
    cat = FilingItem.model_fields["category"].default
    return FilingItem(
        id=fid, symbol=f"{base}.NS", company=(r.get("sm_name") or "").strip(),
        filed_at=_parse_ist(r.get("an_dt"), r.get("sort_date"), r.get("exchdisstime")),
        subject=subject, body=(r.get("attchmntText") or "").strip(), attachment_url=(r.get("attchmntFile") or None),
        category=cat, signal_score=CATEGORY_BASE_SIGNAL[cat], seq_id=seq, source=source).finalize()  # type: ignore[arg-type]


@dataclass
class FilingsResult:
    items: list[FilingItem]
    source: str  # live | cache | cache_stale | fallback_seed | empty
    warning: Optional[str] = None


def fetch_filings(symbol: str, days: int = 1, ttl_s: int = 600) -> FilingsResult:
    """Fallback chain: fresh cache → live NSE → last-good real snapshot → synthetic seed (portfolio only)."""
    base = bare_symbol(symbol)
    win_start = datetime.now(IST) - timedelta(days=days)
    q_key, last_key = f"nse:{base}:{days}", f"nse_lastgood:{base}"

    def build(raw: list[dict], src: str) -> list[FilingItem]:
        seen, out = set(), []
        for r in raw:
            k = _row_key(r)
            if k in seen:
                continue
            seen.add(k)
            it = map_nse_row(r, base, src)
            if it.filed_at >= win_start - timedelta(hours=1):
                out.append(it)
        return sorted(out, key=lambda x: x.filed_at, reverse=True)

    if (c := _disk_get(q_key, ttl_s)) is not None:
        return FilingsResult(build(c, "cache"), "cache")
    warn = None
    try:
        raw = _nse.fetch(base, days)
        _disk_put(q_key, raw)
        merged = {_row_key(r): r for r in (_disk_get_stale(last_key) or [])}  # rolling last-good store (cap 300)
        merged.update({_row_key(r): r for r in raw})
        _disk_put(last_key, sorted(merged.values(), key=lambda r: str(r.get("an_dt")), reverse=True)[:300])
        return FilingsResult(build(raw, "nse"), "live")
    except Exception as e:  # noqa: BLE001
        warn = f"NSE fetch failed for {base}: {e}"
        log.warning(warn)
    if (lg := _disk_get_stale(last_key)):
        items = build(lg, "cache")
        return FilingsResult(items, "cache_stale", warn + " – serving last-good snapshot")
    from fallback_data import has_seed, seed_filings

    if has_seed(base):
        return FilingsResult(seed_filings(base, hours=days * 24), "fallback_seed", warn + " – serving DEMO seed data")
    return FilingsResult([], "empty", warn)


def fetch_filings_batch(symbols: list[str], days: int = 1) -> tuple[list[FilingItem], list[str], dict[str, str]]:
    from concurrent.futures import ThreadPoolExecutor

    warnings: list[str] = []
    items: list[FilingItem] = []
    sources: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=3) as ex:  # NSE rate-limits aggressively
        for sym, res in zip(symbols, ex.map(lambda s: fetch_filings(s, days), symbols)):
            items += res.items
            sources[bare_symbol(sym)] = res.source
            if res.warning:
                warnings.append(res.warning)
    return sorted(items, key=lambda x: x.filed_at, reverse=True), warnings, sources


# ═════════════════════════ 3. Screener.in primary parser ═════════════════════════
SCREENER = "https://www.screener.in"
_NUM = re.compile(r"-?\d[\d,]*\.?\d*")


def _num(txt: str) -> Optional[float]:
    t = txt.replace("\xa0", " ").replace("%", "").strip()
    if not t or t in {"-", "—"}:
        return None
    m = _NUM.search(t)
    try:
        return float(m.group().replace(",", "")) if m else None
    except ValueError:
        return None


def _parse_table(section) -> dict[str, dict[str, Optional[float]]]:
    """<section id=…><table class=data-table> → {row label: {period header: value}}."""
    if section is None:
        return {}
    tbl = section.find("table", class_="data-table") or section.find("table")
    if tbl is None:
        return {}
    heads = [th.get_text(" ", strip=True) for th in tbl.select("thead th")][1:]
    out: dict[str, dict[str, Optional[float]]] = {}
    for tr in tbl.select("tbody tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        label = re.sub(r"[\s\xa0]*\+\s*$", "", tds[0].get_text(" ", strip=True)).strip()  # drop expander "+"
        if label:
            out[label] = {h: _num(td.get_text(strip=True)) for h, td in zip(heads, tds[1:])}
    return out


def _clean_anchor_text(a) -> str:
    """Anchor text without nested <div> (e.g. 'from bse')."""
    clone = BeautifulSoup(str(a), "lxml").find("a")
    for d in clone.find_all(["div", "span", "small"]):
        d.decompose()
    return clone.get_text(" ", strip=True)


def _abs(href: str) -> str:
    return SCREENER + href if href.startswith("/") else href


def _parse_documents(soup: BeautifulSoup) -> tuple[list[DocLink], list[DocLink]]:
    """#documents → Annual reports (direct PDF links) and Concall *Transcript* links only
    (Notes/PPT/REC are ignored; `button` tags without href are skipped by construction)."""
    ars: list[DocLink] = []
    transcripts: list[DocLink] = []
    sec = soup.find(id="documents")
    if sec is None:
        return ars, transcripts
    for block in sec.select("div.documents"):
        h = block.find("h3")
        heading = (h.get_text(" ", strip=True) if h else "").lower()
        classes = " ".join(block.get("class", []))
        if "annual" in heading or "annual-reports" in classes:
            for a in block.select("ul li a[href]"):
                href = _abs(a["href"])
                title = _clean_anchor_text(a) or "Annual Report"
                if href.startswith("http"):
                    ars.append(DocLink(title=title, url=href, date=title, kind="annual_report"))
        elif "concall" in heading or "concalls" in classes:
            for li in block.select("ul li"):
                d = li.find("div")
                period = d.get_text(" ", strip=True) if d else ""
                for a in li.find_all("a", href=True):
                    if "transcript" in a.get_text(" ", strip=True).lower():
                        href = _abs(a["href"])
                        if href.startswith("http"):
                            transcripts.append(DocLink(title=f"Concall {period}".strip(), url=href, date=period or None,
                                                       kind="concall_transcript"))
    return ars, transcripts  # Screener lists newest first


def _screener_get(base: str, basis: str) -> Optional[str]:
    url = f"{SCREENER}/company/{base}/" + ("consolidated/" if basis == "consolidated" else "")
    for i in range(3):
        try:
            r = requests.get(url, headers={"User-Agent": UA, "Accept": "text/html"}, timeout=20)
            if r.status_code == 200:
                return r.text
            if r.status_code == 404:
                return None
        except requests.RequestException as e:
            log.warning("screener %s attempt %d: %s", url, i + 1, e)
        time.sleep(1.5 * (i + 1))
    return None


# ── deterministic derived metrics (no LLM) ──
def _annual_cols(*tables) -> list[str]:
    for t in tables:
        for row in t.values():
            return [p for p in row if p.upper() != "TTM"]
    return []


def _row(tbl: dict, *names: str) -> dict[str, Optional[float]]:
    for n in names:
        if n in tbl:
            return tbl[n]
        for k in tbl:  # tolerate "Sales +", "Net Profit  "
            if k.lower().startswith(n.lower()):
                return tbl[k]
    return {}


def compute_derived(sd: ScreenerData) -> ScreenerDerived:
    d = ScreenerDerived()
    pl, bs, cf, q = sd.profit_loss, sd.balance_sheet, sd.cash_flow, sd.quarters
    d.is_financial = "Financing Profit" in pl or any(k.lower().startswith("deposits") for k in bs)
    if d.is_financial:
        d.notes.append("Bank/NBFC: D/E and CFO are not comparable; use NIM/NPA/CASA from filings instead.")

    sales, pat = _row(pl, "Sales", "Revenue"), _row(pl, "Net Profit")
    cols = _annual_cols(pl)
    # 3Y Sales CAGR: needs 4 annual points (t-3 … t)
    pts = [(p, sales.get(p)) for p in cols if sales.get(p) is not None]
    if len(pts) >= 4 and pts[-4][1] and pts[-4][1] > 0 and pts[-1][1] and pts[-1][1] > 0:
        d.sales_cagr_3y_pct = round(((pts[-1][1] / pts[-4][1]) ** (1 / 3) - 1) * 100, 1)
        d.sales_cagr_periods = [pts[-4][0], pts[-1][0]]
    else:
        d.notes.append("Sales CAGR: fewer than 4 annual data points or non-positive base.")

    # 3Y cumulative CFO / 3Y cumulative PAT
    cfo = _row(cf, "Cash from Operating Activity")
    common = [p for p in cols if p in cfo and cfo[p] is not None and pat.get(p) is not None][-3:]
    if len(common) == 3:
        s_pat = sum(pat[p] for p in common)  # type: ignore[misc]
        s_cfo = sum(cfo[p] for p in common)  # type: ignore[misc]
        d.cash_conversion_periods = common
        if s_pat > 0:
            d.cash_conversion_3y = round(s_cfo / s_pat, 2)
        else:
            d.notes.append("Cash conversion n/a: cumulative 3Y PAT ≤ 0.")
    else:
        d.notes.append("Cash conversion: <3 overlapping annual periods of CFO & PAT.")

    # Latest OPM % – prefer the latest quarter, else latest annual
    for tbl, label in ((q, "quarter"), (pl, "annual")):
        r = _row(tbl, "OPM %", "Financing Margin %")
        vals = [(p, v) for p, v in r.items() if v is not None and p.upper() != "TTM"]
        if vals:
            d.latest_opm_period, d.latest_opm_pct = vals[-1]
            break

    # Debt-to-Equity = Borrowings / (Equity Capital + Reserves), latest annual
    bor, eq, res = _row(bs, "Borrowings"), _row(bs, "Equity Capital"), _row(bs, "Reserves")
    for p in reversed(_annual_cols(bs)):
        if bor.get(p) is not None and eq.get(p) is not None and res.get(p) is not None and (eq[p] + res[p]) > 0:  # type: ignore[operator]
            d.debt_to_equity, d.de_period = round(bor[p] / (eq[p] + res[p]), 2), p  # type: ignore[operator]
            break
    return d


def _is_financial_soup(soup: BeautifulSoup) -> bool:
    pl, qt = _parse_table(soup.find(id="profit-loss")), _parse_table(soup.find(id="quarters"))
    bs = _parse_table(soup.find(id="balance-sheet"))
    return ("Financing Profit" in pl or "Financing Margin %" in pl or "Financing Margin %" in qt
            or any(k.lower().startswith("deposits") for k in bs))


def fetch_screener(symbol: str, ttl_s: int = 6 * 3600) -> ScreenerData:
    """Consolidated first; standalone when consolidated P&L is empty (e.g. HDFCBANK-type or standalone-only cos).
    Raises ValueError if Screener has no usable page (API layer → 404)."""
    base = bare_symbol(symbol)
    key = f"screener:{base}"
    if (c := _disk_get(key, ttl_s)):
        return ScreenerData(**c)

    def usable(sp) -> bool:
        return bool(_parse_table(sp.find(id="profit-loss")) or _parse_table(sp.find(id="quarters")))

    soup, basis, prefer_note = None, "consolidated", None
    for b in ("consolidated", "standalone"):
        html = _screener_get(base, b)
        if not html:
            continue
        cand = BeautifulSoup(html, "lxml")
        if usable(cand):
            soup, basis = cand, b
            break
    # Banks/NBFCs: consolidated tables fold in non-lending subsidiaries and distort Financing Margin % /
    # ratios (HDFCBANK consolidated read -6% vs 14% standalone) → prefer the standalone page.
    if soup is not None and basis == "consolidated" and _is_financial_soup(soup):
        html = _screener_get(base, "standalone")
        if html and usable(alt := BeautifulSoup(html, "lxml")):
            soup, basis = alt, "standalone"
            prefer_note = "Bank/NBFC: standalone Screener page used for margins/ratios (consolidated can be distorted)."
    if soup is None:
        if (stale := _disk_get_stale(key)):
            return ScreenerData(**stale)
        raise ValueError(f"Screener has no usable financial tables for {base}")

    ratios = {}
    for li in soup.select("#top-ratios li"):
        n, v = li.find("span", class_="name"), li.find("span", class_="value")
        if n and v:
            ratios[n.get_text(strip=True)] = re.sub(r"\s+", " ", v.get_text(" ", strip=True))
    ars, calls = _parse_documents(soup)
    about, h1 = soup.select_one(".company-profile .about p"), soup.find("h1")
    sd = ScreenerData(
        symbol=f"{base}.NS", basis=basis, name=h1.get_text(strip=True) if h1 else base,  # type: ignore[arg-type]
        ratios=ratios, about=about.get_text(" ", strip=True) if about else "",
        quarters=_parse_table(soup.find(id="quarters")), profit_loss=_parse_table(soup.find(id="profit-loss")),
        balance_sheet=_parse_table(soup.find(id="balance-sheet")), cash_flow=_parse_table(soup.find(id="cash-flow")),
        ratios_table=_parse_table(soup.find(id="ratios")), annual_reports=ars[:5], concalls=calls[:12])
    sd.derived = compute_derived(sd)
    if prefer_note:
        sd.derived.notes.append(prefer_note)
    _disk_put(key, json.loads(sd.model_dump_json()))
    return sd


# ═════════════════════════ 4. Concall PDF page-tagged extractor ═════════════════════════
_cdb_lock = threading.Lock()


def _cdb() -> sqlite3.Connection:
    c = sqlite3.connect(CONCALL_DB, timeout=10, check_same_thread=False)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS concall_docs(url TEXT PRIMARY KEY, symbol TEXT, period TEXT, n_pages INTEGER,
        status TEXT, extractor TEXT, fetched_at REAL);
    CREATE TABLE IF NOT EXISTS concall_pages(url TEXT, page INTEGER, text TEXT, PRIMARY KEY(url, page));
    CREATE INDEX IF NOT EXISTS ix_cd_sym ON concall_docs(symbol);""")
    return c


@dataclass
class ConcallDoc:
    period: str
    url: str
    status: str  # ok | no_text | download_failed | not_pdf
    pages: list[tuple[int, str]] = field(default_factory=list)  # (1-based page, text)
    from_cache: bool = False


def _clean(t: str) -> str:
    t = re.sub(r"-\n(?=[a-z])", "", t)  # de-hyphenate line breaks
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{2,}", "\n", t)).strip()


def _extract_pdf(content: bytes) -> tuple[list[str], str]:
    from pypdf import PdfReader

    pages: list[str] = []
    try:
        pages = [_clean(p.extract_text() or "") for p in PdfReader(io.BytesIO(content)).pages]
    except Exception as e:  # noqa: BLE001
        log.warning("pypdf failed: %s", e)
    if pages and sum(map(len, pages)) / len(pages) >= 200:
        return pages, "pypdf"
    try:  # second opinion on awkward layouts
        import pdfplumber

        with pdfplumber.open(io.BytesIO(content)) as pdf:
            alt = [_clean(p.extract_text() or "") for p in pdf.pages]
        if sum(map(len, alt)) > sum(map(len, pages)):
            return alt, "pdfplumber"
    except Exception as e:  # noqa: BLE001
        log.warning("pdfplumber unavailable/failed: %s", e)
    return pages, "pypdf"


def load_concall(symbol: str, link: DocLink, retry_failed_after_s: int = 6 * 3600) -> ConcallDoc:
    """Download once, extract once; SQLite is the permanent store (transcripts never change)."""
    period = link.date or link.title
    with _cdb_lock, _cdb() as c:
        row = c.execute("SELECT status, fetched_at FROM concall_docs WHERE url=?", (link.url,)).fetchone()
        if row and (row[0] == "ok" or time.time() - row[1] < retry_failed_after_s):
            pages = c.execute("SELECT page, text FROM concall_pages WHERE url=? ORDER BY page", (link.url,)).fetchall()
            return ConcallDoc(period, link.url, row[0], pages, True)
    status, pages, extractor = "download_failed", [], ""
    try:
        r = requests.get(link.url, headers={"User-Agent": UA, "Referer": SCREENER + "/", "Accept": "application/pdf,*/*"},
                         timeout=45)
        r.raise_for_status()
        if len(r.content) > 30 * 1024 * 1024:
            raise ValueError("PDF > 30MB")
        if not r.content.lstrip()[:5].startswith(b"%PDF"):
            status = "not_pdf"
        else:
            texts, extractor = _extract_pdf(r.content)
            pages = [(i + 1, t) for i, t in enumerate(texts) if t]
            status = "ok" if sum(len(t) for _, t in pages) > 1500 else "no_text"  # scanned → no_text
    except Exception as e:  # noqa: BLE001
        log.warning("concall download failed %s: %s", link.url, e)
    with _cdb_lock, _cdb() as c:
        c.execute("INSERT OR REPLACE INTO concall_docs VALUES(?,?,?,?,?,?,?)",
                  (link.url, bare_symbol(symbol), period, len(pages), status, extractor, time.time()))
        c.execute("DELETE FROM concall_pages WHERE url=?", (link.url,))
        c.executemany("INSERT INTO concall_pages VALUES(?,?,?)", [(link.url, p, t) for p, t in pages])
    return ConcallDoc(period, link.url, status, pages, False)


_GUIDE_KW = ("guidance", "expect", "outlook", "target", "going forward", "next year", "next quarter", "fy2", "fy 2", "aspir",
             "double-digit", "double digit", "margin", "growth", "capex", "order book", "pipeline", "visibility", "confident")
_QA_FIRST = ("first question", "our first question", "next question", "question-and-answer session", "question and answer session")


def _qa_start(pages: list[tuple[int, str]]) -> Optional[int]:
    """Page number where analyst Q&A begins (moderator intros that merely announce Q&A on p1–2 are skipped)."""
    for pno, t in pages:
        tl = " ".join(t.lower().split())  # line wraps can split phrases ("first\nquestion")
        if any(k in tl for k in ("first question", "our first question")) or (pno > 2 and any(k in tl for k in _QA_FIRST)):
            return pno
    return None


def _score_page(pno: int, text: str, qa_start: Optional[int]) -> float:
    tl = " ".join(text.lower().split())
    s = min(sum(tl.count(k) for k in _GUIDE_KW), 15) + min(tl.count("%") + tl.count("crore") + tl.count(" bps"), 8) * 0.4
    in_opening = qa_start is None or pno < qa_start
    if in_opening and pno <= 6:
        s += 4  # management opening remarks carry the prepared narrative
    if qa_start is not None and pno >= qa_start:
        s += 2  # analyst Q&A is where guidance gets pressed
    return s


def concall_context(symbol: str, links: list[DocLink], max_docs: int = 4, char_budget: int = 60_000
                    ) -> tuple[str, list[ConcallDoc]]:
    """Most-recent `max_docs` transcripts → one text block of `[Concall {Period}, p.{N}]`-tagged pages,
    prioritising opening remarks + guidance-dense Q&A pages within `char_budget` (split evenly per call)."""
    docs = [load_concall(symbol, l) for l in links[:max_docs]]
    good = [d for d in docs if d.status == "ok"]
    if not good:
        return "", docs
    per_doc, blocks = char_budget // len(good), []
    for d in good:
        qa = _qa_start(d.pages)
        ranked = sorted(d.pages, key=lambda p: _score_page(p[0], p[1], qa), reverse=True)
        keep, used = [], 0
        for pno, t in ranked:
            if used + len(t) > per_doc and keep:
                continue
            keep.append((pno, t))
            used += len(t)
        for pno, t in sorted(keep):  # restore reading order
            blocks.append(f"[Concall {d.period}, p.{pno}]\n{t}")
    return "\n\n".join(blocks), docs


# ═════════════════════════ CLI ═════════════════════════
def warm(days: int = 30) -> None:
    """Pre-fetch REAL data for the demo portfolio so a blocked-IP deploy still serves genuine last-good data."""
    for st in DEFAULT_PORTFOLIO:
        r = fetch_filings(st.symbol, days=days, ttl_s=0)
        try:
            sd = fetch_screener(st.symbol, ttl_s=0)
            sc = f"screener:{sd.basis}"
        except Exception as e:  # noqa: BLE001
            sc = f"screener FAIL {e}"
        print(f"{st.base:10s} filings={len(r.items):3d} [{r.source}] {sc}")
        time.sleep(1.0)


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    arg = sys.argv[1] if len(sys.argv) > 1 else "TCS.NS"
    if arg == "warm":
        warm()
    else:
        print(get_eod_snapshot(arg).model_dump_json(indent=1))
        fr = fetch_filings(arg, days=7)
        print(len(fr.items), "filings", fr.source, fr.warning or "")
        sd = fetch_screener(arg)
        print(sd.basis, sd.derived.model_dump() if sd.derived else None)
