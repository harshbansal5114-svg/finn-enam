"use client";

import { Cites } from "./Cite";
import { cx, num, shortPeriod } from "@/lib/format";
import type { MetricPoint } from "@/lib/types";

const TREND = { up: ["▲", "text-emerald-700"], down: ["▼", "text-red-700"], flat: ["▬", "text-slate-400"], "n/a": ["", ""] } as const;

const fmt = (v: number | null | undefined, unit: string) => (v == null ? "—" : unit === "₹ Cr" ? num(v, 0) : unit === "%" ? `${v.toFixed(1)}%` : unit === "×" ? v.toFixed(2) : num(v, 0));

/**
 * Metrics arrive keyed by period; annual and quarterly series have different columns, so rows are grouped by
 * identical period sets and each group gets its own header row.
 */
export default function MetricTable({ metrics, maxCols = 5 }: { metrics: MetricPoint[]; maxCols?: number }) {
  const groups = new Map<string, MetricPoint[]>();
  for (const m of metrics) {
    const k = Object.keys(m.values).slice(-maxCols).join("|");
    groups.set(k, [...(groups.get(k) ?? []), m]);
  }
  return (
    <div className="space-y-3">
      {[...groups.entries()].map(([k, rows]) => {
        const cols = k.split("|").filter(Boolean);
        return (
          <div key={k} className="-mx-1 overflow-x-auto px-1 no-scrollbar">
            <table className="w-full min-w-[300px] text-[12px] tabular-nums">
              <thead>
                <tr className="text-2xs uppercase tracking-wide text-slate-500">
                  <th className="py-1 pr-2 text-left font-semibold">Metric</th>
                  {cols.map((c) => <th key={c} className="px-1.5 py-1 text-right font-semibold">{shortPeriod(c)}</th>)}
                </tr>
              </thead>
              <tbody>
                {rows.map((m) => {
                  const [arrow, tone] = TREND[m.trend] ?? TREND["n/a"];
                  return (
                    <tr key={m.label} className="border-t border-slate-100 align-top">
                      <td className="py-1.5 pr-2">
                        <div className="font-medium text-slate-700">
                          {m.label} {arrow && <span className={cx("text-[9px]", tone)}>{arrow}</span>}
                        </div>
                        {m.cagr_3y_pct != null && <div className="text-[10px] text-slate-400">3Y CAGR {m.cagr_3y_pct.toFixed(1)}%</div>}
                        <Cites citation={m.citation} className="mt-0.5 opacity-80" />
                      </td>
                      {cols.map((c) => <td key={c} className="px-1.5 py-1.5 text-right text-slate-900">{fmt(m.values[c], m.unit)}</td>)}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        );
      })}
    </div>
  );
}
