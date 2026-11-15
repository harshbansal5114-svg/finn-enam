"""FINN schemas (Pydantic v2). Single source of truth shared by fetchers, LLM router and API."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ───────────────────────── Portfolio ─────────────────────────
class PortfolioStock(Strict):
    symbol: str  # always NSE form, e.g. "TCS.NS"
    name: str
    sector: str
    cap: Literal["large", "mid", "small"]

    @field_validator("symbol")
    @classmethod
    def _ns(cls, v: str) -> str:
        v = v.upper()
        if not v.endswith(".NS"):
            v += ".NS"
        return v

    @property
    def base(self) -> str:  # "TCS" for NSE / screener URLs
        return self.symbol.removesuffix(".NS")


# Demo portfolio only – NO pipeline depends on this list (generalisation rule).
DEFAULT_PORTFOLIO: list[PortfolioStock] = [
    PortfolioStock(symbol="RELIANCE.NS", name="Reliance Industries", sector="Conglomerate", cap="large"),
    PortfolioStock(symbol="TCS.NS", name="Tata Consultancy Services", sector="IT Services", cap="large"),
    PortfolioStock(symbol="HDFCBANK.NS", name="HDFC Bank", sector="Banking", cap="large"),
    PortfolioStock(symbol="TRENT.NS", name="Trent", sector="Retail", cap="large"),
    PortfolioStock(symbol="ETERNAL.NS", name="Eternal (ex-Zomato)", sector="Consumer Internet", cap="large"),
    PortfolioStock(symbol="DIXON.NS", name="Dixon Technologies", sector="Electronics Mfg", cap="mid"),
    PortfolioStock(symbol="SUNPHARMA.NS", name="Sun Pharmaceutical", sector="Pharma", cap="large"),
    PortfolioStock(symbol="BEL.NS", name="Bharat Electronics", sector="Defence", cap="large"),
    PortfolioStock(symbol="KPITTECH.NS", name="KPIT Technologies", sector="Auto Software", cap="mid"),
    PortfolioStock(symbol="TATAELXSI.NS", name="Tata Elxsi", sector="Design Services", cap="mid"),
    PortfolioStock(symbol="INDHOTEL.NS", name="Indian Hotels", sector="Hospitality", cap="large"),
    PortfolioStock(symbol="CDSL.NS", name="CDSL", sector="Capital Markets", cap="mid"),
    PortfolioStock(symbol="POLYCAB.NS", name="Polycab India", sector="Cables & Wires", cap="mid"),
    PortfolioStock(symbol="KAYNES.NS", name="Kaynes Technology", sector="Electronics Mfg", cap="mid"),
    PortfolioStock(symbol="VARROC.NS", name="Varroc Engineering", sector="Auto Components", cap="small"),
    PortfolioStock(symbol="SUZLON.NS", name="Suzlon Energy", sector="Renewables", cap="mid"),
]


# ───────────────────────── Filings ─────────────────────────
class Category(str, Enum):
    results = "results"
    ma = "M&A"
    fund_raise = "fund_raise"
    management_change = "management_change"
    litigation = "litigation"
    insider_trading = "insider_trading"
    earnings_call = "earnings_call"
    investor_meetings = "investor_meetings"
    credit_rating = "credit_rating"
    dividend = "dividend"
    bonus_split = "bonus_split"
    capex = "capex"
    press_release = "press_release"
    postal_ballot = "postal_ballot"
    agm_egm = "agm_egm"
    pledging = "pledging"
    unclassified = "unclassified"


# Prior signal by category: used as a free pre-LLM baseline and as a sanity clamp on LLM scores.
CATEGORY_BASE_SIGNAL: dict[Category, int] = {
    Category.results: 9, Category.ma: 9, Category.management_change: 8, Category.litigation: 8,
    Category.fund_raise: 7, Category.capex: 7, Category.credit_rating: 6, Category.pledging: 6,
    Category.dividend: 5, Category.bonus_split: 5, Category.insider_trading: 4,
    Category.press_release: 4, Category.earnings_call: 4, Category.agm_egm: 3,
    Category.postal_ballot: 3, Category.investor_meetings: 2, Category.unclassified: 1,
}
NOISE_THRESHOLD = 4  # signal_score < this ⇒ is_noise


class FilingItem(Strict):
    id: str  # stable hash(symbol|date|subject|file) – cache + dedupe key
    symbol: str
    company: str = ""
    filed_at: datetime
    subject: str  # NSE "desc"
    body: str = ""  # NSE "attchmntText"
    attachment_url: Optional[str] = None
    category: Category = Category.unclassified
    signal_score: int = Field(default=1, ge=1, le=10)
    is_noise: bool = True
    one_liner: Optional[str] = None  # LLM summary (only generated for non-noise)
    enrichment: Optional[dict] = None  # e.g. results table / person bio payload
    classified_by: Literal["rule", "llm", "none"] = "none"
    seq_id: Optional[str] = None  # NSE sequence id (stable upstream key)
    source: Literal["nse", "cache", "fallback_seed"] = "nse"  # UI must badge fallback_seed as demo data

    def finalize(self) -> "FilingItem":
        self.is_noise = self.signal_score < NOISE_THRESHOLD
        return self


class ResultsQuarter(Strict):
    """Headline numbers extracted from a results filing (₹ crore)."""
    revenue: Optional[float] = None
    ebitda: Optional[float] = None
    pbt: Optional[float] = None
    pat: Optional[float] = None


class ResultsSnapshot(Strict):
    source: Literal["screener_quarters", "filing_pdf_llm", "unavailable"] = "screener_quarters"
    basis: Literal["consolidated", "standalone"] = "consolidated"
    ebitda_label: str = "EBITDA"  # "Financing Profit" for banks/NBFCs
    margin_takeaway: str = ""
    period_match: bool = True  # False ⇒ Screener hadn't yet published the filing's quarter
    notes: list[str] = Field(default_factory=list)
    current: ResultsQuarter
    prev_qtr: Optional[ResultsQuarter] = None
    year_ago: Optional[ResultsQuarter] = None
    period_label: str = ""
    qoq_pct: dict[str, Optional[float]] = Field(default_factory=dict)
    yoy_pct: dict[str, Optional[float]] = Field(default_factory=dict)


class ManagementChangeBio(Strict):
    person: Optional[str] = None
    role: Optional[str] = None
    action: Literal["appointed", "resigned", "retired", "other"] = "other"
    effective_date: Optional[str] = None
    bullets: list[str] = Field(default_factory=list)  # ≤3 verified bullets (prior role · pedigree · why it matters)
    verified: bool = False
    profile_url: Optional[str] = None
    note: str = ""  # e.g. "No verified LinkedIn profile found"


# ───────────────────────── EOD alerts ─────────────────────────
class AlertThresholds(Strict):
    price_move_pct: float = Field(default=5.0, gt=0)  # |1D return| ≥ X%
    volume_ratio: float = Field(default=2.0, gt=0)  # vol / 20D avg ≥ X
    near_52w_pct: float = Field(default=2.0, ge=0)  # within X% of 52W high/low


class EODSnapshot(Strict):
    symbol: str
    as_of: str  # YYYY-MM-DD of last session
    close: float
    prev_close: float
    return_1d_pct: float
    volume: int
    avg_volume_20d: float
    volume_ratio: float
    high_52w: float
    low_52w: float
    dist_from_52w_high_pct: float  # ≤ 0 (e.g. -3.2 means 3.2% below high)
    dist_from_52w_low_pct: float  # ≥ 0
    error: Optional[str] = None


class EODAlert(Strict):
    symbol: str
    as_of: str
    triggers: list[Literal["price_up", "price_down", "volume_spike", "near_52w_high", "near_52w_low"]]
    price_move_pct: float
    volume_ratio: float
    close: float
    thresholds: AlertThresholds
    headline: str

    @classmethod
    def evaluate(cls, snap: EODSnapshot, th: AlertThresholds) -> Optional["EODAlert"]:
        if snap.error:
            return None
        t: list = []
        if snap.return_1d_pct >= th.price_move_pct:
            t.append("price_up")
        if snap.return_1d_pct <= -th.price_move_pct:
            t.append("price_down")
        if snap.volume_ratio >= th.volume_ratio:
            t.append("volume_spike")
        if abs(snap.dist_from_52w_high_pct) <= th.near_52w_pct:
            t.append("near_52w_high")
        if snap.dist_from_52w_low_pct <= th.near_52w_pct:
            t.append("near_52w_low")
        if not t:
            return None
        head = f"{snap.symbol.removesuffix('.NS')} {snap.return_1d_pct:+.1f}% on {snap.volume_ratio:.1f}× volume"
        return cls(symbol=snap.symbol, as_of=snap.as_of, triggers=t, price_move_pct=snap.return_1d_pct,
                   volume_ratio=snap.volume_ratio, close=snap.close, thresholds=th, headline=head)


# ───────────────────────── Screener (primary fundamentals) ─────────────────────────
class DocLink(Strict):
    title: str
    url: str
    date: Optional[str] = None
    kind: Literal["annual_report", "concall_transcript", "concall_ppt", "concall_notes", "concall_recording", "other"]


class ScreenerDerived(Strict):
    """Computed in pure Python from the parsed tables – zero LLM arithmetic."""
    is_financial: bool = False  # banks/NBFCs: D/E and CFO are not comparable
    cash_conversion_3y: Optional[float] = None  # Σ CFO(3Y) / Σ PAT(3Y)
    cash_conversion_periods: list[str] = Field(default_factory=list)
    sales_cagr_3y_pct: Optional[float] = None
    sales_cagr_periods: list[str] = Field(default_factory=list)  # [start, end]
    latest_opm_pct: Optional[float] = None  # EBITDA / OPM % (Financing Margin % for financials)
    latest_opm_period: Optional[str] = None
    debt_to_equity: Optional[float] = None
    de_period: Optional[str] = None
    notes: list[str] = Field(default_factory=list)


class ScreenerData(Strict):
    symbol: str
    basis: Literal["consolidated", "standalone"]
    name: str = ""
    ratios: dict[str, str] = Field(default_factory=dict)  # top-ratio strip (Market Cap, P/E, ROCE…)
    about: str = ""
    # table → row label → period label → value
    quarters: dict[str, dict[str, Optional[float]]] = Field(default_factory=dict)
    profit_loss: dict[str, dict[str, Optional[float]]] = Field(default_factory=dict)
    balance_sheet: dict[str, dict[str, Optional[float]]] = Field(default_factory=dict)
    cash_flow: dict[str, dict[str, Optional[float]]] = Field(default_factory=dict)
    ratios_table: dict[str, dict[str, Optional[float]]] = Field(default_factory=dict)
    annual_reports: list[DocLink] = Field(default_factory=list)
    concalls: list[DocLink] = Field(default_factory=list)
    derived: Optional[ScreenerDerived] = None
    fetched_at: datetime = Field(default_factory=datetime.utcnow)


# ───────────────────────── Pull research report ─────────────────────────
# Citation contract: every analytical claim carries one or more bracketed primary-source tags in a `citation` string:
#   [Concall Oct 2025, p.3] · [AR FY2025, p.41] · [Screener Quarterly P&L] · [Screener Annual P&L]
#   [Screener Balance Sheet] · [Screener Cash Flow] · [Screener Ratios] · [Screener About]
# pull_engine.validate_* drops any claim whose tags don't resolve to a page/table that was actually supplied to the model.
class CitedPoint(Strict):
    text: str
    citation: str
    grounded: bool = True  # False ⇒ a number in `text` was not found in the cited facts (UI shows ⚠)
    by: Literal["llm", "rule"] = "llm"  # "rule" = deterministic Python point (also the LLM-outage fallback)


class BusinessSnapshot(Strict):
    what_it_does: str
    segments: list[str] = Field(default_factory=list)
    revenue_mix: dict[str, float] = Field(default_factory=dict)  # segment → % of revenue (only when stated in a primary source)
    citation: str


class FinancialSnapshot(Strict):
    """Headline numbers at a glance – all deterministic (₹ crore unless noted)."""
    as_of_quarter: str = ""
    basis: Literal["consolidated", "standalone"] = "consolidated"
    is_financial: bool = False
    ebitda_label: str = "EBITDA"  # "Financing Profit" for banks/NBFCs
    ttm_revenue_cr: Optional[float] = None
    ttm_ebitda_cr: Optional[float] = None
    ebitda_margin_pct: Optional[float] = None  # TTM EBITDA / TTM revenue (Financing Margin for financials)
    ttm_pat_cr: Optional[float] = None
    roce_pct: Optional[float] = None
    roe_pct: Optional[float] = None
    debt_to_equity: Optional[float] = None  # None for banks/NBFCs (not comparable)
    sales_cagr_3y_pct: Optional[float] = None
    notes: list[str] = Field(default_factory=list)
    citation: str = ""


class MetricPoint(Strict):
    label: str  # "Revenue", "EBITDA margin %", "ROCE %", "Debt/Equity"…
    unit: str
    values: dict[str, Optional[float]]  # period → value (computed in Python, never by LLM)
    cagr_3y_pct: Optional[float] = None
    trend: Literal["up", "down", "flat", "n/a"] = "n/a"
    citation: str = ""


class FinancialMetrics(Strict):
    """Deterministic – computed from ScreenerData in pure Python."""
    headline: list[MetricPoint] = Field(default_factory=list)
    balance_sheet: list[MetricPoint] = Field(default_factory=list)  # debt, D/E, working-cap days
    cash_quality: list[MetricPoint] = Field(default_factory=list)  # CFO, PAT, CFO/PAT, FCF proxy
    flags: list[str] = Field(default_factory=list)  # rule-based red flags (each ends with its citation tag)


class NarrativeAuditRow(Strict):
    """One management promise vs what the filings later showed."""
    metric_or_theme: str
    promised_guidance: str  # paraphrase of the guidance (verbatim quote was verified against the cited page)
    actual_delivered: str
    verdict: Literal["Beat", "Met", "Missed", "In Progress"]
    citation: str  # "[Concall Oct 2025, p.3] [Screener Quarterly P&L]" (promise source + delivery source)
    period: str = ""  # the call / annual report the promise was made in, e.g. "Oct 2025" or "FY2025"
    grounded: bool = True  # False ⇒ a number in actual_delivered isn't in the supplied quarterly facts


class NarrativeVsNumbers(Strict):
    rows: list[NarrativeAuditRow] = Field(default_factory=list, max_length=8)
    summary: str = ""  # deterministic tally line
    credibility_score: Optional[int] = Field(default=None, ge=1, le=10)  # round(10 × (Beat+Met)/(Beat+Met+Missed))
    tally: dict[str, int] = Field(default_factory=dict)
    basis: Literal["concall", "annual_report", "none"] = "none"


class CashQualityCheck(Strict):
    verdict: Literal["strong", "adequate", "weak", "insufficient_data"]
    cfo_to_pat_3y_cum: Optional[float] = None  # Σ CFO(3Y) / Σ PAT(3Y)
    periods: list[str] = Field(default_factory=list)
    cfo_3y_cr: Optional[float] = None
    pat_3y_cr: Optional[float] = None
    fcf_3y_sum_cr: Optional[float] = None  # proxy: CFO − (ΔNet block + ΔCWIP + depreciation)
    watch_flag: bool = False  # True when conversion < 0.75×
    commentary: str = ""
    wc_observations: list[str] = Field(default_factory=list)  # working-capital cycle notes (tagged)
    citation: str = ""


class GuidanceItem(Strict):
    metric: str  # "Revenue growth", "EBITDA margin", "Capex"
    statement: str
    horizon: str = ""
    quote: Optional[str] = None  # verbatim (≤25 words) – verified against the cited page text
    citation: str
    period: str = ""
    source: Literal["concall", "annual_report"] = "concall"


class TrajectoryItem(Strict):
    label: str
    detail: str
    citation: str


class Trajectory(Strict):
    accelerating: list[TrajectoryItem] = Field(default_factory=list)
    compressing: list[TrajectoryItem] = Field(default_factory=list)


class BullBear(Strict):
    bull: list[CitedPoint] = Field(default_factory=list, max_length=3)
    bear: list[CitedPoint] = Field(default_factory=list, max_length=3)


class PullResearchReport(Strict):
    symbol: str
    company: str = ""
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    basis: Literal["consolidated", "standalone"] = "consolidated"
    mode: Literal["concall", "annual_report", "financials_only"] = "financials_only"
    concall_pages: int = 0
    concall_periods: list[str] = Field(default_factory=list)
    sources_used: list[str] = Field(default_factory=list)  # URLs of Screener page / AR / concalls actually read
    business: Optional[BusinessSnapshot] = None
    snapshot: Optional[FinancialSnapshot] = None
    metrics: Optional[FinancialMetrics] = None
    trajectory: Optional[Trajectory] = None
    narrative_vs_numbers: Optional[NarrativeVsNumbers] = None
    cash_quality: Optional[CashQualityCheck] = None
    guidance: list[GuidanceItem] = Field(default_factory=list)
    bull_bear: Optional[BullBear] = None
    data_gaps: list[str] = Field(default_factory=list)  # honest "couldn't find X" notes
    llm_degraded: bool = False  # True ⇒ an LLM stage failed/was skipped; deterministic fallbacks used
    llm_cost_usd: float = 0.0
    latency_ms: int = 0
    cache_hit: bool = False
