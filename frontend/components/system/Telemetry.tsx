"use client";

import { Card, CardSkeleton, Chip, SectionLabel, Stat } from "../ui";
import { fetchTelemetry } from "@/lib/api";
import { useResource } from "@/lib/hooks";
import { useApp } from "@/lib/store";
import { cx, fmtDT, num, usd } from "@/lib/format";
import { useEffect } from "react";
import type { Telemetry } from "@/lib/types";

function Split({ label, fast, deep, fmt }: { label: string; fast: number; deep: number; fmt: (v: number) => string }) {
  const t = fast + deep;
  const fp = t > 0 ? (fast / t) * 100 : 0;
  return (
    <div>
      <div className="flex items-baseline justify-between text-[11px] text-slate-500">
        <span className="font-semibold uppercase tracking-wide">{label}</span>
        <span className="tabular-nums">{t > 0 ? `${fp.toFixed(0)}% fast tier` : "no calls yet"}</span>
      </div>
      <div className="mt-1 flex h-2.5 overflow-hidden rounded-full bg-slate-200">
        {t > 0 && <><div className="bg-emerald-600" style={{ width: `${fp}%` }} /><div className="bg-slate-800" style={{ width: `${100 - fp}%` }} /></>}
      </div>
      <div className="mt-1 flex justify-between text-[11px] tabular-nums text-slate-600">
        <span><span className="mr-1 inline-block h-2 w-2 rounded-sm bg-emerald-600" />Fast {fmt(fast)}</span>
        <span><span className="mr-1 inline-block h-2 w-2 rounded-sm bg-slate-800" />Deep {fmt(deep)}</span>
      </div>
    </div>
  );
}

function Body({ t }: { t: Telemetry }) {
  const { llm, pull_cache: pc } = t;
  const tier = (k: "fast" | "deep") => {
    const rows = llm.by_task.filter((r) => r.tier === k);
    return { calls: rows.reduce((s, r) => s + r.calls, 0), cost: rows.reduce((s, r) => s + (r.cost_usd ?? 0), 0) };
  };
  const f = tier("fast");
  const d = tier("deep");
  return (
    <>
      <Card className="p-3">
        <div className="grid grid-cols-2 gap-x-3 gap-y-3 sm:grid-cols-4">
          <Stat label="USD saved by cache" value={usd(t.total_saved_usd)} tone="text-emerald-700" sub="LLM + report cache" />
          <Stat label="LLM spend" value={usd(t.total_cost_usd)} sub={`${num(llm.live_calls)} live calls`} />
          <Stat label="LLM cache hit" value={`${(llm.cache_hit_rate * 100).toFixed(0)}%`} sub={`${num(llm.cache_hits)} / ${num(llm.total_calls)} calls`} />
          <Stat label="Report cache hit" value={`${(pc.hit_rate * 100).toFixed(0)}%`} sub={`${num(pc.hits)} hit · ${num(pc.misses)} miss`} />
        </div>
      </Card>

      <Card className="space-y-3 p-3">
        <div className="flex items-center justify-between">
          <div className="text-2xs font-bold uppercase tracking-[0.08em] text-slate-500">Model routing split</div>
          <Chip tone="slate">{llm.provider}</Chip>
        </div>
        <Split label="Calls" fast={f.calls} deep={d.calls} fmt={(v) => num(v)} />
        <Split label="Cost" fast={f.cost} deep={d.cost} fmt={usd} />
        <div className="grid gap-2 border-t border-slate-100 pt-2.5 text-[11.5px] sm:grid-cols-2">
          <div><span className="font-semibold text-emerald-700">FAST</span> <span className="font-mono text-slate-700">{llm.models.fast}</span><div className="text-slate-500">bulk classification &amp; extraction</div></div>
          <div><span className="font-semibold text-slate-800">DEEP</span> <span className="font-mono text-slate-700">{llm.models.deep}</span><div className="text-slate-500">multi-quarter narrative synthesis</div></div>
        </div>
      </Card>

      <Card className="p-3">
        <div className="grid grid-cols-2 gap-x-3 gap-y-3 sm:grid-cols-4">
          <Stat label="Avg live latency" value={llm.avg_live_latency_ms ? `${(llm.avg_live_latency_ms / 1000).toFixed(1)}s` : "—"} />
          <Stat label="Report: cached vs fresh" value={`${num(pc.avg_hit_ms)}ms`} sub={`vs ${(pc.avg_generation_ms / 1000).toFixed(1)}s generate`} />
          <Stat label="Tokens in / out" value={`${num(llm.tokens_in)} / ${num(llm.tokens_out)}`} />
          <Stat label="Failed calls" value={num(llm.failed_calls)} tone={llm.failed_calls ? "text-red-700" : undefined} sub={`${num(pc.reports_cached)} reports cached`} />
        </div>
      </Card>

      {llm.by_task.length > 0 && (
        <Card className="overflow-hidden">
          <table className="w-full text-[12px] tabular-nums">
            <thead className="bg-slate-50 text-2xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="py-1.5 pl-3 text-left font-semibold">Task</th>
                <th className="px-2 py-1.5 text-right font-semibold">Calls</th>
                <th className="px-2 py-1.5 text-right font-semibold">Cached</th>
                <th className="py-1.5 pr-3 text-right font-semibold">Cost</th>
              </tr>
            </thead>
            <tbody>
              {llm.by_task.map((r) => (
                <tr key={`${r.task}-${r.tier}`} className="border-t border-slate-100">
                  <td className="py-1.5 pl-3">
                    <span className={cx("mr-1.5 inline-block rounded px-1 text-[9px] font-bold uppercase", r.tier === "fast" ? "bg-emerald-50 text-emerald-800" : "bg-slate-800 text-white")}>{r.tier}</span>
                    <span className="font-mono text-[11px] text-slate-700">{r.task}</span>
                  </td>
                  <td className="px-2 py-1.5 text-right">{num(r.calls)}</td>
                  <td className="px-2 py-1.5 text-right text-slate-500">{num(r.cache_hits ?? 0)}</td>
                  <td className="py-1.5 pr-3 text-right">{usd(r.cost_usd ?? 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
}

export default function TelemetryPanel() {
  const { epoch, tab } = useApp();
  const res = useResource<Telemetry>(`tele:${epoch}`, () => fetchTelemetry(), { area: "telemetry" });
  const { reload } = res;
  // live dashboard: poll every 15s while the System tab is visible
  useEffect(() => {
    if (tab !== "system") return;
    const i = setInterval(reload, 15_000);
    return () => clearInterval(i);
  }, [tab, reload]);

  const t = res.data?.data;
  return (
    <section className="space-y-2">
      <SectionLabel right={<span className="text-[11px] tabular-nums text-slate-500">{t ? `Updated ${fmtDT(t.generated_at)}` : ""}</span>}>LLM cost &amp; cache telemetry</SectionLabel>
      {res.loading && !t && <CardSkeleton rows={3} />}
      {res.error && !t && (
        <Card className="px-3 py-5 text-center">
          <p className="text-[12.5px] font-semibold text-slate-700">Telemetry is live-only</p>
          <p className="mx-auto mt-1 max-w-xs text-[11.5px] text-slate-500">The backend is unreachable, so the cost ledger isn&apos;t shown (a stale ledger would be misleading). Everything else is served from the cached snapshot.</p>
          <button onClick={reload} className="mt-2.5 rounded border border-slate-300 px-3 py-1 text-[12px] font-semibold text-slate-700 active:bg-slate-100">Retry</button>
        </Card>
      )}
      {t && <Body t={t} />}
    </section>
  );
}
