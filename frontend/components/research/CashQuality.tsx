"use client";

import { Card, Chip, Collapsible, SectionLabel, WarnLine } from "../ui";
import { CiteText } from "./Cite";
import MetricTable from "./MetricTable";
import { cx, inr, mult, num } from "@/lib/format";
import type { CashQualityCheck, FinancialMetrics } from "@/lib/types";

const WATCH = 0.75;
const GAUGE_MAX = 1.5;

export default function CashQuality({ cq, metrics }: { cq: CashQualityCheck; metrics: FinancialMetrics | null }) {
  const r = cq.cfo_to_pat_3y_cum;
  const has = r != null;
  const severe = has && r < 0.5;
  const tone = !has ? "text-slate-400" : r < WATCH ? (severe ? "text-red-700" : "text-amber-600") : "text-emerald-700";
  const barTone = !has ? "bg-slate-300" : r < WATCH ? (severe ? "bg-red-600" : "bg-amber-500") : "bg-emerald-600";
  const pos = has ? Math.min(100, (Math.max(r, 0) / GAUGE_MAX) * 100) : 0;
  const wc = cq.wc_observations;
  const bs = metrics?.balance_sheet ?? [];
  const cash = metrics?.cash_quality ?? [];
  const flags = metrics?.flags ?? [];

  return (
    <section className="space-y-2">
      <SectionLabel>Cash quality &amp; balance-sheet watch</SectionLabel>
      <Card className={cx("p-3", cq.watch_flag && (severe ? "border-red-300" : "border-amber-300"))}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-2xs font-bold uppercase tracking-[0.08em] text-slate-500">3Y cumulative CFO &divide; PAT</div>
            <div className={cx("mt-0.5 text-[34px] font-black leading-none tabular-nums", tone)}>{has ? mult(r) : "n/a"}</div>
            <div className="mt-1 text-[11px] text-slate-500">{cq.periods.length ? `${cq.periods[0]} – ${cq.periods[cq.periods.length - 1]}` : "Insufficient overlapping years"}</div>
          </div>
          <div className="flex flex-col items-end gap-1.5">
            {cq.watch_flag ? (
              <Chip tone={severe ? "red" : "amber"} className="px-2 py-1 text-[11px]">
                <span aria-hidden>&#9888;</span> Cash-conversion watch &lt; {WATCH.toFixed(2)}&times;
              </Chip>
            ) : (
              <Chip tone={cq.verdict === "strong" || cq.verdict === "adequate" ? "green" : "slate"} className="px-2 py-1 text-[11px]">
                {cq.verdict === "insufficient_data" ? "Not assessable" : `Cash conversion: ${cq.verdict}`}
              </Chip>
            )}
          </div>
        </div>

        {has && (
          <div className="mt-3">
            <div className="relative h-2 rounded-full bg-slate-200">
              <div className={cx("h-2 rounded-full", barTone)} style={{ width: `${pos}%` }} />
              <span className="absolute -top-1 bottom-[-4px] w-px bg-slate-900" style={{ left: `${(WATCH / GAUGE_MAX) * 100}%` }} />
            </div>
            <div className="relative mt-1 h-3 text-[10px] tabular-nums text-slate-500">
              <span className="absolute left-0">0&times;</span>
              <span className="absolute -translate-x-1/2 font-semibold text-slate-700" style={{ left: `${(WATCH / GAUGE_MAX) * 100}%` }}>0.75&times; watch line</span>
              <span className="absolute right-0">{GAUGE_MAX.toFixed(1)}&times;+</span>
            </div>
          </div>
        )}

        <div className="mt-3 grid grid-cols-3 gap-2 border-t border-slate-100 pt-2.5">
          {[
            ["3Y CFO", cq.cfo_3y_cr],
            ["3Y PAT", cq.pat_3y_cr],
            ["3Y FCF proxy", cq.fcf_3y_sum_cr],
          ].map(([l, v]) => (
            <div key={l as string}>
              <div className="text-2xs font-semibold uppercase tracking-wide text-slate-500">{l as string}</div>
              <div className={cx("text-[14px] font-semibold tabular-nums", (v as number | null) != null && (v as number) < 0 ? "text-red-700" : "text-slate-900")}>
                {v == null ? "—" : inr(v as number, 0)}
                {v != null && <span className="ml-0.5 text-[10px] font-medium text-slate-400">Cr</span>}
              </div>
            </div>
          ))}
        </div>
        <p className="mt-2.5 text-[12px] leading-snug text-slate-700"><CiteText text={cq.commentary} /></p>
        <p className="mt-1 text-[10px] text-slate-400">FCF proxy = CFO &minus; (&Delta;net block + &Delta;CWIP + depreciation); Screener doesn&apos;t publish capex directly.</p>
      </Card>

      {wc.length > 0 && (
        <Card className="p-3">
          <div className="text-2xs font-bold uppercase tracking-[0.08em] text-slate-500">Working-capital observations</div>
          <ul className="mt-1.5 space-y-1.5">
            {wc.map((w, i) => (
              <li key={i} className="flex gap-1.5 text-[12px] text-slate-700">
                <span aria-hidden className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-amber-500" />
                <CiteText text={w} />
              </li>
            ))}
          </ul>
        </Card>
      )}

      {flags.length > 0 && (
        <Card className="border-amber-200 bg-amber-50/50 p-3">
          <div className="text-2xs font-bold uppercase tracking-[0.08em] text-amber-800">Rule-based flags</div>
          <ul className="mt-1.5 space-y-1.5">
            {flags.map((f, i) => (
              <WarnLine key={i}><CiteText text={f} /></WarnLine>
            ))}
          </ul>
        </Card>
      )}

      {(cash.length > 0 || bs.length > 0) && (
        <Collapsible title="Cash-flow & balance-sheet history" summary={`${cash.length + bs.length} series`}>
          <div className="space-y-4">
            {cash.length > 0 && <MetricTable metrics={cash} />}
            {bs.length > 0 && <MetricTable metrics={bs} />}
          </div>
          <p className="mt-2 text-[10px] text-slate-400">All values computed in Python from Screener tables (&#8377; Cr) &mdash; no LLM arithmetic. {num(cash.length + bs.length)} series.</p>
        </Collapsible>
      )}
    </section>
  );
}
