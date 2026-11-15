"use client";

import { Chip, Pct } from "../ui";
import { WarnIcon } from "../icons";
import { num } from "@/lib/format";
import type { ResultsSnapshot } from "@/lib/types";

const SRC: Record<ResultsSnapshot["source"], string> = {
  screener_quarters: "Screener quarterly (filed data)",
  filing_pdf_llm: "Filing PDF · LLM-extracted",
  unavailable: "Unavailable",
};

export default function ResultsTable({ r }: { r: ResultsSnapshot }) {
  const rows: [string, "revenue" | "ebitda" | "pbt" | "pat"][] = [
    ["Revenue", "revenue"],
    [r.ebitda_label, "ebitda"],
    ["PBT", "pbt"],
    ["PAT", "pat"],
  ];
  const empty = r.source === "unavailable";
  return (
    <div className="mt-2.5 rounded border border-slate-200 bg-slate-50/60">
      <div className="flex items-center justify-between gap-2 px-2.5 pt-2">
        <span className="text-2xs font-bold uppercase tracking-[0.08em] text-slate-500">Results snapshot &middot; {r.period_label || "—"}</span>
        <Chip tone="slate">{r.basis}</Chip>
      </div>
      {!empty && (
        <table className="mt-1 w-full text-[12px] tabular-nums">
          <thead>
            <tr className="text-2xs uppercase tracking-wide text-slate-500">
              <th className="px-2.5 py-1 text-left font-semibold">&#8377; Cr</th>
              <th className="px-1.5 py-1 text-right font-semibold">Current</th>
              <th className="px-1.5 py-1 text-right font-semibold">QoQ %</th>
              <th className="px-2.5 py-1 text-right font-semibold">YoY %</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([label, k]) => (
              <tr key={k} className="border-t border-slate-200/70">
                <td className="px-2.5 py-1.5 font-medium text-slate-700">{label}</td>
                <td className="px-1.5 py-1.5 text-right font-semibold text-slate-900">{num(r.current[k], 0)}</td>
                <td className="px-1.5 py-1.5 text-right"><Pct v={r.qoq_pct[k]} /></td>
                <td className="px-2.5 py-1.5 text-right"><Pct v={r.yoy_pct[k]} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="space-y-1 px-2.5 pb-2 pt-1.5">
        {r.margin_takeaway && <p className="text-[11px] font-medium text-slate-700">{r.margin_takeaway}</p>}
        <p className="text-[10px] text-slate-500">Source: {SRC[r.source]}</p>
        {!r.period_match && !empty && (
          <p className="flex items-start gap-1 text-[11px] text-amber-800"><WarnIcon className="mt-0.5 shrink-0" />Latest available quarter shown; the filing&apos;s own quarter isn&apos;t published on Screener yet.</p>
        )}
        {r.notes.map((n, i) => (
          <p key={i} className="flex items-start gap-1 text-[11px] text-amber-800"><WarnIcon className="mt-0.5 shrink-0" />{n}</p>
        ))}
      </div>
    </div>
  );
}
