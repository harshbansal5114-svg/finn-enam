"""Health check for the FINN data layer.   python verify_pipeline.py [TICKER ...] [--days 7] [--docs 4] [--no-concalls]
Defaults: DIXON TRENT HDFCBANK(standalone-fallback test) BEL(ad-hoc). Exit code 1 if any hard check fails."""
from __future__ import annotations

import argparse
import logging
import sys
import time

from data_fetcher import bare_symbol, concall_context, fetch_filings, fetch_screener, get_eod_snapshot

DEFAULTS = ["DIXON", "TRENT", "HDFCBANK", "BEL"]
NOTE = {"HDFCBANK": "standalone/bank test", "BEL": "ad-hoc ticker"}


def check(sym: str, days: int, docs: int, do_concalls: bool) -> dict:
    base, row = bare_symbol(sym), {"sym": bare_symbol(sym), "fail": [], "t": {}}

    t = time.time()
    eod = get_eod_snapshot(base)
    row["t"]["eod"] = time.time() - t
    if eod.error and not eod.close:
        row["eod"] = "FAIL"; row["fail"].append("eod")
    else:
        row["eod"] = f"₹{eod.close:,.1f} {eod.return_1d_pct:+.1f}% v×{eod.volume_ratio:.1f}" + ("*" if eod.error else "")

    t = time.time()
    fr = fetch_filings(base, days=days)
    row["t"]["nse"] = time.time() - t
    row["nse"] = f"{len(fr.items)} [{fr.source}]"
    if fr.source == "empty":
        row["fail"].append("nse")  # seed/stale still count as a pass: the app stays usable

    t = time.time()
    links = []
    try:
        sd = fetch_screener(base)
        d = sd.derived
        links = sd.concalls
        row["basis"] = sd.basis[:5]
        row["ccr"] = "n/a" if d.cash_conversion_3y is None else f"{d.cash_conversion_3y:.2f}×"
        row["cagr"] = "n/a" if d.sales_cagr_3y_pct is None else f"{d.sales_cagr_3y_pct:.1f}%"
        row["opm"] = "n/a" if d.latest_opm_pct is None else f"{d.latest_opm_pct:.0f}%"
        row["de"] = "n/a(fin)" if d.is_financial else ("n/a" if d.debt_to_equity is None else f"{d.debt_to_equity:.2f}")
        row["docs"] = f"{len(sd.annual_reports)}AR/{len(sd.concalls)}CC"
        if not sd.annual_reports and not sd.concalls:
            row["fail"].append("docs")
    except Exception as e:  # noqa: BLE001
        row.update(basis="-", ccr="FAIL", cagr="-", opm="-", de="-", docs="-")
        row["fail"].append(f"screener: {e}")
    row["t"]["screener"] = time.time() - t

    t = time.time()
    if do_concalls and links:
        text, cdocs = concall_context(base, links, max_docs=docs)
        ok = [c for c in cdocs if c.status == "ok"]
        pages = sum(len(c.pages) for c in ok)
        row["calls"] = f"{pages}p/{len(ok)}of{len(cdocs)}" + ("·cached" if cdocs and all(c.from_cache for c in cdocs) else "")
        if not ok:
            row["fail"].append("concall (" + ",".join(sorted({c.status for c in cdocs})) + ")")
        elif "[Concall " not in text:
            row["fail"].append("page tags missing")
    else:
        row["calls"] = "skip" if not do_concalls else "0 transcripts"
    row["t"]["concall"] = time.time() - t
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="*", default=DEFAULTS)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--docs", type=int, default=4)
    ap.add_argument("--no-concalls", action="store_true")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO if a.v else logging.ERROR)

    rows = [check(s, a.days, a.docs, not a.no_concalls) for s in a.tickers]
    cols = [("Ticker", "sym", 9), ("EOD close/1D/vol", "eod", 24), (f"NSE {a.days}d [src]", "nse", 20), ("Basis", "basis", 6),
            ("CCR 3Y", "ccr", 7), ("Sales CAGR", "cagr", 10), ("OPM", "opm", 5), ("D/E", "de", 8), ("Docs", "docs", 9),
            ("Concall pages", "calls", 16)]
    line = "  ".join(f"{h:<{w}}" for h, _, w in cols)
    print("\n" + line + "  Status\n" + "-" * (len(line) + 8))
    for r in rows:
        st = "OK" if not r["fail"] else "FAIL"
        print("  ".join(f"{str(r.get(k, '')):<{w}}" for _, k, w in cols) + f"  {st}" + (f" ({NOTE[r['sym']]})" if r["sym"] in NOTE else ""))
    print("\n* = stale EOD   [src]: live | cache | cache_stale | fallback_seed(demo) | empty")
    for r in rows:
        if r["fail"]:
            print(f"  ✗ {r['sym']}: " + "; ".join(r["fail"]))
        if a.v:
            print(f"  ⏱ {r['sym']}: " + ", ".join(f"{k} {v:.1f}s" for k, v in r["t"].items()))
    return 1 if any(r["fail"] for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
