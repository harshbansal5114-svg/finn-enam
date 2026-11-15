"use client";

import { Card, Chip, Collapsible, SectionLabel, Stat } from "../ui";
import { Cites, CiteText, Ungrounded } from "./Cite";
import MetricTable from "./MetricTable";
import { cx, inr, mult, pct } from "@/lib/format";
import type { BullBear, BusinessSnapshot, FinancialMetrics, FinancialSnapshot, GuidanceItem, PullReport, Trajectory } from "@/lib/types";

// ───────────── business ─────────────
export function Business({ b }: { b: BusinessSnapshot }) {
  const mix = Object.entries(b.revenue_mix).sort((a, c) => c[1] - a[1]);
  return (
    <Card className="p-3">
      <SectionLabel right={<Cites citation={b.citation} />}>Business</SectionLabel>
      <p className="mt-1.5 text-[13px] leading-snug text-slate-800">{b.what_it_does}</p>
      {b.segments.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {b.segments.map((s) => <Chip key={s} tone="slate">{s}</Chip>)}
        </div>
      )}
      {mix.length > 0 && (
        <div className="mt-2.5">
          <div className="flex h-1.5 overflow-hidden rounded-full bg-slate-200">
            {mix.map(([k, v], i) => <div key={k} style={{ width: `${v}%`, opacity: 1 - i * 0.18 }} className="bg-slate-800" title={`${k} ${v}%`} />)}
          </div>
          <div className="mt-1 flex flex-wrap gap-x-3 text-[11px] tabular-nums text-slate-600">
            {mix.map(([k, v]) => <span key={k}>{k} {v}%</span>)}
          </div>
        </div>
      )}
    </Card>
  );
}

// ───────────── snapshot metrics ─────────────
export function SnapshotMetrics({ s, metrics }: { s: FinancialSnapshot; metrics: FinancialMetrics | null }) {
  const tiles: [string, string, string?][] = [
    ["TTM revenue", s.ttm_revenue_cr == null ? "—" : `${inr(s.ttm_revenue_cr, 0)} Cr`],
    [`${s.ebitda_label} margin`, s.ebitda_margin_pct == null ? "—" : `${s.ebitda_margin_pct.toFixed(1)}%`, s.ttm_ebitda_cr != null ? `${inr(s.ttm_ebitda_cr, 0)} Cr TTM` : undefined],
    ["TTM PAT", s.ttm_pat_cr == null ? "—" : `${inr(s.ttm_pat_cr, 0)} Cr`],
    ["3Y sales CAGR", pct(s.sales_cagr_3y_pct)],
    ["ROCE", s.is_financial ? "n/a" : pct(s.roce_pct, 1, false), s.is_financial ? "lender" : undefined],
    ["ROE", pct(s.roe_pct, 1, false)],
    ["Debt / equity", s.is_financial ? "n/a" : mult(s.debt_to_equity), s.is_financial ? "lender" : undefined],
  ];
  return (
    <section className="space-y-2">
      <SectionLabel right={<span className="text-[11px] text-slate-500">{s.as_of_quarter || "—"} &middot; {s.basis}</span>}>Snapshot metrics</SectionLabel>
      <Card className="p-3">
        <div className="grid grid-cols-2 gap-x-3 gap-y-3 sm:grid-cols-4">
          {tiles.map(([l, v, sub]) => <Stat key={l} label={l} value={v} sub={sub} />)}
        </div>
        {s.notes.length > 0 && <p className="mt-2.5 border-t border-slate-100 pt-2 text-[11px] text-slate-500">{s.notes.join(" · ")}</p>}
        <div className="mt-2"><Cites citation={s.citation} /></div>
      </Card>
      {metrics && metrics.headline.length > 0 && (
        <Collapsible title="Headline metric history" summary={`${metrics.headline.length} series`}>
          <MetricTable metrics={metrics.headline} />
        </Collapsible>
      )}
    </section>
  );
}

// ───────────── trajectory ─────────────
export function TrajectoryView({ t }: { t: Trajectory }) {
  const cols: { title: string; items: Trajectory["accelerating"]; tone: string; arrow: string }[] = [
    { title: "Accelerating", items: t.accelerating, tone: "text-emerald-700", arrow: "▲" },
    { title: "Compressing", items: t.compressing, tone: "text-red-700", arrow: "▼" },
  ];
  return (
    <section className="space-y-2">
      <SectionLabel>Trajectory</SectionLabel>
      <div className="grid gap-2 md:grid-cols-2">
        {cols.map((c) => (
          <Card key={c.title} className="p-3">
            <div className={cx("text-2xs font-bold uppercase tracking-[0.08em]", c.tone)}>{c.arrow} {c.title}</div>
            {c.items.length === 0 ? (
              <p className="mt-1.5 text-[12px] text-slate-400">Nothing crossed the threshold.</p>
            ) : (
              <ul className="mt-1.5 space-y-2">
                {c.items.map((i, k) => (
                  <li key={k}>
                    <div className="text-[12.5px] font-semibold text-slate-900">{i.label}</div>
                    <div className="text-[12px] leading-snug text-slate-600">{i.detail}</div>
                    <Cites citation={i.citation} className="mt-0.5" />
                  </li>
                ))}
              </ul>
            )}
          </Card>
        ))}
      </div>
    </section>
  );
}

// ───────────── bull vs bear ─────────────
export function BullBearView({ bb }: { bb: BullBear }) {
  const side = (title: string, pts: BullBear["bull"], tone: string, dot: string) => (
    <Card className="p-3">
      <div className={cx("text-2xs font-bold uppercase tracking-[0.08em]", tone)}>{title}</div>
      <ul className="mt-1.5 space-y-2.5">
        {pts.length === 0 && <li className="text-[12px] text-slate-400">No cited points available.</li>}
        {pts.map((p, i) => (
          <li key={i} className="flex gap-2">
            <span aria-hidden className={cx("mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full", dot)} />
            <div className="min-w-0">
              <p className="text-[12.5px] leading-snug text-slate-800">
                {p.text} {!p.grounded && <Ungrounded />}
              </p>
              <div className="mt-0.5 flex flex-wrap items-center gap-1">
                <Cites citation={p.citation} />
                {p.by === "rule" && <Chip tone="slate" title="Deterministic rule on Screener numbers (no LLM)">Rule</Chip>}
              </div>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
  return (
    <section className="space-y-2">
      <SectionLabel>Bull vs. bear</SectionLabel>
      <div className="grid gap-2 md:grid-cols-2">
        {side("Bull case", bb.bull, "text-emerald-700", "bg-emerald-600")}
        {side("Bear case", bb.bear, "text-red-700", "bg-red-600")}
      </div>
    </section>
  );
}

// ───────────── guidance list ─────────────
export function GuidanceList({ items }: { items: GuidanceItem[] }) {
  return (
    <Collapsible title="Extracted management guidance" summary={`${items.length} verified`}>
      <ul className="divide-y divide-slate-100">
        {items.map((g, i) => (
          <li key={i} className="py-2 first:pt-0 last:pb-0">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[12.5px] font-semibold text-slate-900">{g.metric}</span>
              <span className="text-[11px] text-slate-500">{g.horizon || "horizon n/a"}</span>
            </div>
            <p className="text-[12px] text-slate-700">{g.statement}</p>
            {g.quote && <p className="mt-0.5 border-l-2 border-slate-300 pl-2 text-[11.5px] italic text-slate-500">&ldquo;{g.quote}&rdquo;</p>}
            <Cites citation={g.citation} className="mt-1" />
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[10px] text-slate-400">Each item&apos;s quote and figures were verified against the cited page before display.</p>
    </Collapsible>
  );
}

// ───────────── data gaps ─────────────
export function DataGaps({ r }: { r: PullReport }) {
  const gaps = r.data_gaps;
  const pivot = (g: string) => /annual report/i.test(g) && /no concall|unreadable|machine-readable/i.test(g);
  if (!gaps.length && !r.llm_degraded) return null;
  return (
    <section className="space-y-2">
      <SectionLabel>Data gaps &amp; provenance</SectionLabel>
      <Card className="divide-y divide-slate-100">
        {r.llm_degraded && (
          <div className="flex items-start gap-2 bg-amber-50 p-3 text-[12px] text-amber-900">
            <Chip tone="amber">LLM degraded</Chip>
            <span>An LLM stage failed or was skipped. Deterministic fallbacks are shown; no figure here depends on a failed stage.</span>
          </div>
        )}
        {gaps.map((g, i) => (
          <div key={i} className="flex items-start gap-2 p-3 text-[12px] text-slate-700">
            {pivot(g) ? <Chip tone="violet">AR pivot</Chip> : <span aria-hidden className="mt-0.5 text-slate-400">&#9432;</span>}
            <span>{g}</span>
          </div>
        ))}
      </Card>
    </section>
  );
}
