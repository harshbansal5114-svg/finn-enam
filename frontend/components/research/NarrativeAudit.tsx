"use client";

import { Card, Chip, SectionLabel } from "../ui";
import { Cites, Ungrounded } from "./Cite";
import { cx } from "@/lib/format";
import type { NarrativeAuditRow, NarrativeVsNumbers, Verdict } from "@/lib/types";
import type { Tone } from "@/lib/categories";

const V: Record<Verdict, { tone: Tone; bar: string; border: string }> = {
  Beat: { tone: "green", bar: "bg-emerald-600", border: "border-l-emerald-600" },
  Met: { tone: "blue", bar: "bg-blue-600", border: "border-l-blue-600" },
  Missed: { tone: "red", bar: "bg-red-600", border: "border-l-red-600" },
  "In Progress": { tone: "slate", bar: "bg-slate-400", border: "border-l-slate-300" },
};
const ORDER: Verdict[] = ["Beat", "Met", "Missed", "In Progress"];

function Credibility({ score }: { score: number | null }) {
  const tone = score == null ? "bg-slate-100 text-slate-500 border-slate-300" : score >= 7 ? "bg-emerald-600 text-white border-emerald-700" : score >= 4 ? "bg-amber-500 text-white border-amber-600" : "bg-red-600 text-white border-red-700";
  return (
    <div className={cx("flex h-[58px] w-[58px] shrink-0 flex-col items-center justify-center rounded-md border", tone)} title="round(10 × (Beat + Met) ÷ (Beat + Met + Missed)) — computed in Python, not by the LLM">
      <div className="text-[22px] font-black leading-none tabular-nums">{score ?? "—"}</div>
      <div className="mt-0.5 text-[9px] font-bold uppercase tracking-wider opacity-90">/10 cred</div>
    </div>
  );
}

function Row({ r }: { r: NarrativeAuditRow }) {
  const v = V[r.verdict] ?? V["In Progress"];
  return (
    <Card className={cx("border-l-4 p-3", v.border)}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-[13px] font-bold leading-tight text-slate-900">{r.metric_or_theme}</div>
          {r.period && <div className="text-[11px] text-slate-500">Guided in {r.period}</div>}
        </div>
        <Chip tone={v.tone} className={r.verdict === "In Progress" ? "border-dashed" : ""}>{r.verdict}</Chip>
      </div>
      <div className="mt-2 grid gap-2 md:grid-cols-2">
        <div className="rounded bg-slate-50 p-2">
          <div className="text-2xs font-bold uppercase tracking-[0.08em] text-slate-500">Promised guidance</div>
          <p className="mt-0.5 text-[12.5px] leading-snug text-slate-800">{r.promised_guidance}</p>
        </div>
        <div className="rounded bg-slate-50 p-2">
          <div className="flex items-center gap-1.5 text-2xs font-bold uppercase tracking-[0.08em] text-slate-500">
            Actual delivered {!r.grounded && <Ungrounded />}
          </div>
          <p className="mt-0.5 text-[12.5px] leading-snug text-slate-800">{r.actual_delivered}</p>
        </div>
      </div>
      <Cites citation={r.citation} className="mt-2" />
    </Card>
  );
}

export default function NarrativeAudit({ nvn }: { nvn: NarrativeVsNumbers }) {
  const tally = ORDER.map((k) => ({ k, n: nvn.tally[k] ?? 0 }));
  const total = tally.reduce((s, t) => s + t.n, 0);
  const src = nvn.basis === "concall" ? "Concall transcripts" : nvn.basis === "annual_report" ? "Annual Report" : "No guidance source";
  return (
    <section className="space-y-2">
      <SectionLabel right={<Chip tone={nvn.basis === "annual_report" ? "violet" : "blue"}>{src}</Chip>}>Narrative vs. numbers &middot; guidance audit</SectionLabel>
      <Card className="p-3">
        <div className="flex items-center gap-3">
          <Credibility score={nvn.credibility_score} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[12px] font-semibold tabular-nums">
              {tally.map(({ k, n }) => (
                <span key={k} className="inline-flex items-center gap-1 text-slate-700">
                  <span className={cx("h-2 w-2 rounded-sm", V[k].bar)} />{k} <span className="text-slate-900">{n}</span>
                </span>
              ))}
            </div>
            <div className="mt-1.5 flex h-1.5 overflow-hidden rounded-full bg-slate-200">
              {total > 0 && tally.map(({ k, n }) => n > 0 && <div key={k} className={V[k].bar} style={{ width: `${(n / total) * 100}%` }} />)}
            </div>
            <p className="mt-1.5 text-[11.5px] leading-snug text-slate-500">{nvn.summary}</p>
          </div>
        </div>
      </Card>
      {nvn.rows.length === 0 ? (
        <Card className="px-3 py-4 text-center text-[12px] text-slate-500">No explicit, verifiable guidance was found to audit. See data gaps below.</Card>
      ) : (
        <div className="grid items-start gap-2 lg:grid-cols-2">{nvn.rows.map((r, i) => <Row key={i} r={r} />)}</div>
      )}
    </section>
  );
}
