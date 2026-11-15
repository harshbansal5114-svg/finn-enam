"use client";

import { useMemo, useState } from "react";
import { Card, Chip, Pct, SectionLabel } from "../ui";
import { cx, fmtD, inr, mult, pct } from "@/lib/format";
import type { PortfolioItem, Thresholds } from "@/lib/types";

type Sort = "default" | "move" | "vol";

export default function Portfolio({ items, asOf, ok, th, onOpen, stale }: { items: PortfolioItem[]; asOf: string | null; ok: number; th: Thresholds; onOpen: (t: string) => void; stale: boolean }) {
  const [sort, setSort] = useState<Sort>("default");
  const rows = useMemo(() => {
    const r = [...items];
    if (sort === "move") r.sort((a, b) => Math.abs(b.return_1d_pct ?? 0) - Math.abs(a.return_1d_pct ?? 0));
    if (sort === "vol") r.sort((a, b) => (b.volume_ratio ?? 0) - (a.volume_ratio ?? 0));
    return r;
  }, [items, sort]);

  return (
    <section className="space-y-2">
      <SectionLabel right={<span className="text-[11px] tabular-nums text-slate-500">{ok}/{items.length} priced &middot; {fmtD(asOf)}{stale ? " · snapshot" : ""}</span>}>
        Watchlist &middot; {items.length} stocks
      </SectionLabel>
      <div className="flex gap-1.5">
        {([["default", "Portfolio"], ["move", "|1D %|"], ["vol", "Vol ×"]] as [Sort, string][]).map(([id, l]) => (
          <button key={id} onClick={() => setSort(id)} className={cx("rounded border px-2.5 py-1 text-2xs font-bold uppercase tracking-wide", sort === id ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300 bg-white text-slate-600 active:bg-slate-100")}>
            {l}
          </button>
        ))}
      </div>
      <Card className="overflow-hidden">
        <table className="w-full text-[12.5px] tabular-nums">
          <thead className="bg-slate-50 text-2xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="py-1.5 pl-3 text-left font-semibold">Ticker</th>
              <th className="px-2 py-1.5 text-right font-semibold">Close</th>
              <th className="px-2 py-1.5 text-right font-semibold">1D %</th>
              <th className="px-2 py-1.5 text-right font-semibold">Vol &times;</th>
              <th className="hidden px-2 py-1.5 text-right font-semibold md:table-cell">vs 52W high</th>
              <th className="py-1.5 pr-3" />
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => {
              const breach = (p.return_1d_pct != null && Math.abs(p.return_1d_pct) >= th.price_thresh) || (p.volume_ratio != null && p.volume_ratio >= th.vol_mult);
              const volHot = p.volume_ratio != null && p.volume_ratio >= th.vol_mult;
              return (
                <tr key={p.symbol} onClick={() => onOpen(p.base)} className={cx("cursor-pointer border-t border-slate-100 active:bg-slate-50", breach && "bg-amber-50/60")}>
                  <td className="py-2 pl-3">
                    <div className="flex items-center gap-1.5">
                      <span className={cx("h-4 w-0.5 rounded", breach ? "bg-amber-500" : "bg-transparent")} />
                      <div className="min-w-0">
                        <div className="font-bold leading-tight text-slate-900">{p.base}</div>
                        <div className="max-w-[120px] truncate text-[10px] leading-tight text-slate-500 md:max-w-none">{p.sector}</div>
                      </div>
                    </div>
                  </td>
                  <td className="px-2 py-2 text-right font-medium text-slate-900">{p.close == null ? "—" : inr(p.close)}</td>
                  <td className="px-2 py-2 text-right font-semibold"><Pct v={p.return_1d_pct} /></td>
                  <td className={cx("px-2 py-2 text-right", volHot ? "font-bold text-amber-700" : "text-slate-700")}>{mult(p.volume_ratio, 1)}</td>
                  <td className="hidden px-2 py-2 text-right text-slate-600 md:table-cell">{p.close == null ? "—" : pct(p.dist_from_52w_high_pct)}</td>
                  <td className="py-2 pr-3 text-right">{p.stale && <Chip tone="amber" title="Last good price; live refresh failed">Stale</Chip>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>
      <p className="px-1 text-[10px] text-slate-400">Highlighted rows breach the current thresholds. Tap a row to open its research audit.</p>
    </section>
  );
}
