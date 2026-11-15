"""Last-resort demo filings for the 16 portfolio stocks, used ONLY when NSE blocks the host IP
(common on cloud hosts) AND no real last-good snapshot exists in data/cache.

Chain: live NSE → last-good real snapshot (data/cache) → this seed.
All items are SYNTHETIC: source="fallback_seed", no attachments, no named individuals, no financial
figures. The UI must badge them "demo data". Never generated for ad-hoc tickers.
Best practice: run `python data_fetcher.py warm` where NSE works and commit data/cache/.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from schemas import CATEGORY_BASE_SIGNAL, Category as C, FilingItem

# bare symbol → [(hours_ago, category, NSE-style subject, body, score_delta)]
_SEED: dict[str, tuple[str, list[tuple[float, C, str, str, int]]]] = {
    "RELIANCE": ("Reliance Industries Limited", [
        (3, C.results, "Financial Results", "Outcome of Board Meeting: unaudited consolidated financial results for the quarter ended September 30.", 0),
        (5, C.earnings_call, "Analysts/Institutional Investor Meet/Con. Call Updates", "Audio recording and transcript of the earnings conference call hosted for analysts.", -1),
        (14, C.capex, "Updates", "Board approves investment in a new energy-transition manufacturing facility (giga-factory expansion).", 0),
        (20, C.fund_raise, "Fund Raising", "Allotment of non-convertible debentures on a private placement basis.", 0)]),
    "TCS": ("Tata Consultancy Services Limited", [
        (2, C.results, "Financial Results", "Outcome of Board Meeting: results for the quarter and half year ended September 30; interim dividend declared.", 0),
        (4, C.dividend, "Dividend", "Interim dividend declared; record date fixed for entitlement.", 0),
        (9, C.earnings_call, "Analysts/Institutional Investor Meet/Con. Call Updates", "Transcript of Q2 earnings call with management.", 0),
        (30, C.press_release, "Press Release", "Company announces a multi-year transformation partnership with a global enterprise client.", 0)]),
    "HDFCBANK": ("HDFC Bank Limited", [
        (3, C.results, "Financial Results", "Outcome of Board Meeting: standalone and consolidated results for the quarter ended September 30.", 0),
        (7, C.credit_rating, "Credit Rating", "Rating agency reaffirms the highest-safety rating on the bank's debt instruments.", 0),
        (18, C.fund_raise, "Fund Raising", "Board approves raising of long-term infrastructure bonds, subject to market conditions.", 0),
        (26, C.management_change, "Change in Directors/ Key Managerial Personnel", "Re-appointment of an Independent Director for a second term (sample item).", -1)]),
    "TRENT": ("Trent Limited", [
        (4, C.results, "Financial Results", "Outcome of Board Meeting: unaudited results for the quarter ended September 30.", 0),
        (10, C.capex, "Updates", "Board approves capital outlay for new Westside and Zudio store openings across tier-2 cities.", 0),
        (22, C.press_release, "Press Release", "Company clarifies on media report regarding store-expansion plans.", -1)]),
    "ETERNAL": ("Eternal Limited", [
        (2, C.results, "Financial Results", "Outcome of Board Meeting: results for the quarter ended September 30; shareholder letter published.", 0),
        (6, C.earnings_call, "Analysts/Institutional Investor Meet/Con. Call Updates", "Notice and transcript of earnings call.", 0),
        (16, C.ma, "Acquisition", "Board approves investment in a wholly owned subsidiary for quick-commerce expansion.", 0),
        (27, C.insider_trading, "Disclosure under SEBI PIT Regulations", "Disclosure of ESOP-related transactions by designated persons.", 0)]),
    "DIXON": ("Dixon Technologies (India) Limited", [
        (3, C.results, "Financial Results", "Outcome of Board Meeting: unaudited consolidated results for the quarter ended September 30.", 0),
        (8, C.ma, "Acquisition", "Board approves joint venture with a global ODM for manufacturing under the PLI scheme.", 0),
        (15, C.capex, "Updates", "Approval of capacity expansion at a new facility for mobile and IT hardware.", 0),
        (28, C.press_release, "Press Release", "Company receives approval under a government electronics-component incentive scheme.", 0)]),
    "SUNPHARMA": ("Sun Pharmaceutical Industries Limited", [
        (5, C.results, "Financial Results", "Outcome of Board Meeting: results for the quarter and half year ended September 30.", 0),
        (11, C.litigation, "Action(s) initiated or orders passed", "Regulatory observations received following inspection of a manufacturing facility; response being filed.", 0),
        (19, C.press_release, "Press Release", "Company announces US launch of a new specialty product.", 0)]),
    "BEL": ("Bharat Electronics Limited", [
        (2, C.press_release, "Press Release", "BEL secures new orders for radar and electronic-warfare systems since last disclosure.", 1),
        (6, C.results, "Financial Results", "Outcome of Board Meeting: unaudited results for the quarter ended September 30.", 0),
        (12, C.earnings_call, "Analysts/Institutional Investor Meet/Con. Call Updates", "Intimation and transcript of analyst call.", 0),
        (24, C.dividend, "Dividend", "Interim dividend declared with record date.", 0)]),
    "KPITTECH": ("KPIT Technologies Limited", [
        (4, C.results, "Financial Results", "Outcome of Board Meeting: results for the quarter ended September 30.", 0),
        (9, C.earnings_call, "Analysts/Institutional Investor Meet/Con. Call Updates", "Transcript of earnings call discussing software-defined-vehicle pipeline.", 0),
        (21, C.press_release, "Press Release", "Strategic partnership with a global OEM for vehicle software platform.", 0)]),
    "TATAELXSI": ("Tata Elxsi Limited", [
        (3, C.results, "Financial Results", "Outcome of Board Meeting: results for the quarter ended September 30; dividend considered.", 0),
        (10, C.earnings_call, "Analysts/Institutional Investor Meet/Con. Call Updates", "Transcript of earnings call.", 0),
        (23, C.investor_meetings, "Analysts/Institutional Investor Meet/Con. Call Updates", "Schedule of one-on-one meetings with institutional investors.", 0)]),
    "INDHOTEL": ("The Indian Hotels Company Limited", [
        (5, C.results, "Financial Results", "Outcome of Board Meeting: consolidated results for the quarter ended September 30.", 0),
        (13, C.ma, "Acquisition", "Company signs agreement to acquire a stake in a hospitality asset-owning entity.", 0),
        (25, C.press_release, "Press Release", "Signing of new hotels under the Taj, Vivanta and Gateway brands.", 0)]),
    "CDSL": ("Central Depository Services (India) Limited", [
        (3, C.results, "Financial Results", "Outcome of Board Meeting: results for the quarter ended September 30.", 0),
        (12, C.dividend, "Dividend", "Interim dividend declared with record date.", 0),
        (20, C.press_release, "Press Release", "Demat account additions update for the month.", -1)]),
    "POLYCAB": ("Polycab India Limited", [
        (4, C.results, "Financial Results", "Outcome of Board Meeting: unaudited consolidated results for the quarter ended September 30.", 0),
        (11, C.capex, "Updates", "Board approves capex for capacity expansion in cables and FMEG segment.", 0),
        (22, C.litigation, "Action(s) initiated or orders passed", "Notice received from a tax authority; company evaluating response.", 0)]),
    "KAYNES": ("Kaynes Technology India Limited", [
        (2, C.results, "Financial Results", "Outcome of Board Meeting: results for the quarter ended September 30.", 0),
        (8, C.fund_raise, "Fund Raising", "Board approves raising of funds by way of QIP subject to shareholder approval.", 0),
        (14, C.capex, "Updates", "Approval of OSAT/semiconductor facility investment milestone.", 0),
        (29, C.postal_ballot, "Postal Ballot", "Notice of postal ballot for fund-raising approval.", 0)]),
    "VARROC": ("Varroc Engineering Limited", [
        (5, C.results, "Financial Results", "Outcome of Board Meeting: results for the quarter ended September 30.", 0),
        (13, C.credit_rating, "Credit Rating", "Rating agency upgrades long-term bank facilities rating.", 0),
        (24, C.press_release, "Press Release", "New business wins for lighting programmes with global OEMs.", 0)]),
    "SUZLON": ("Suzlon Energy Limited", [
        (3, C.results, "Financial Results", "Outcome of Board Meeting: results for the quarter ended September 30.", 0),
        (7, C.press_release, "Press Release", "Company bags a new wind-turbine order from an independent power producer.", 1),
        (17, C.credit_rating, "Credit Rating", "Rating upgrade on bank facilities.", 0),
        (26, C.pledging, "Disclosure under SEBI Takeover Regulations", "Release of encumbrance on promoter shares.", 0)]),
}

_NOISE: list[tuple[C, str, str]] = [
    (C.unclassified, "Employee Stock Option Scheme", "Allotment of equity shares pursuant to exercise of stock options."),
    (C.unclassified, "Compliances-Reg. 74 (5) - Depositories and Participants", "Certificate under Regulation 74(5) of SEBI (Depositories and Participants) Regulations."),
    (C.unclassified, "Trading Window", "Intimation of closure of trading window for designated persons."),
    (C.unclassified, "Newspaper Publication", "Copy of newspaper advertisement of financial results."),
]


def has_seed(bare: str) -> bool:
    return bare.upper() in _SEED


def seed_filings(bare: str, hours: float = 24, now: datetime | None = None) -> list[FilingItem]:
    from data_fetcher import IST  # local import avoids a cycle at module load

    bare = bare.upper().removesuffix(".NS")
    if bare not in _SEED:
        return []  # never fabricate for ad-hoc tickers
    name, rows = _SEED[bare]
    now = now or datetime.now(IST)
    h = int(hashlib.sha1(f"{bare}:{now.date()}".encode()).hexdigest(), 16)
    noise = _NOISE[h % len(_NOISE)]
    allrows = [(hrs, c, s, b, d) for hrs, c, s, b, d in rows] + [(1.5 + (h % 7), *noise, 0)]
    out: list[FilingItem] = []
    for i, (hrs, cat, subj, body, delta) in enumerate(allrows):
        if hrs > max(hours, 24):  # keep rows inside window; the 24h default shows all
            continue
        score = max(1, min(10, CATEGORY_BASE_SIGNAL[cat] + delta))
        out.append(FilingItem(
            id=f"seed-{bare}-{now.date()}-{i}", symbol=f"{bare}.NS", company=name,
            filed_at=now - timedelta(hours=hrs), subject=subj, body=body, attachment_url=None,
            category=cat, signal_score=score, classified_by="rule", seq_id=None, source="fallback_seed").finalize())
    return sorted(out, key=lambda x: x.filed_at, reverse=True)
