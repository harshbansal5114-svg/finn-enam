"use client";

import { useMemo, useState } from "react";
import AlertCard from "./AlertCard";
import FilingCard from "./FilingCard";
import NoiseDrawer from "./NoiseDrawer";
import { Card, CardSkeleton, ErrorBox, SectionLabel, SnapshotBadge, Spinner } from "../ui";
import { fetchPortfolio, fetchPush } from "@/lib/api";
import { FILTERS } from "@/lib/categories";
import { cx, fmtDT } from "@/lib/format";
import { useDebounced, useResource } from "@/lib/hooks";
import { useApp } from "@/lib/store";
import type { PortfolioPayload, PushFeed } from "@/lib/types";

export default function PulseTab() {
  const { thresholds, epoch, openResearch, retryLive } = useApp();
  const th = useDebounced(thresholds, 500);
  const [filter, setFilter] = useState("all");

  const push = useResource<PushFeed>(`push:${th.window_hours}:${th.price_thresh}:${th.vol_mult}:${epoch}`, (f) => fetchPush(th, f), { area: "pulse" });
  const port = useResource<PortfolioPayload>(`port:${epoch}`, (f) => fetchPortfolio(f), { area: "portfolio" });

  const feed = push.data?.data;
  const pfBySym = useMemo(() => new Map((port.data?.data.items ?? []).map((i) => [i.symbol, i])), [port.data]);

  const filings = useMemo(() => (feed?.items ?? []).filter((i) => i.kind === "filing" && i.filing), [feed]);
  const filtered = useMemo(() => {
    const f = FILTERS.find((x) => x.id === filter);
    return f?.cats ? filings.filter((i) => f.cats!.includes(i.filing!.category)) : filings;
  }, [filings, filter]);
  const counts = useMemo(() => {
    const c: Record<string, number> = { all: filings.length };
    for (const f of FILTERS) if (f.cats) c[f.id] = filings.filter((i) => f.cats!.includes(i.filing!.category)).length;
    return c;
  }, [filings]);

  if (push.loading && !feed) {
    return (
      <div className="space-y-3">
        <CardSkeleton rows={2} />
        <CardSkeleton rows={4} />
        <CardSkeleton rows={4} />
      </div>
    );
  }
  if (push.error && !feed) return <ErrorBox message={push.error} onRetry={push.reload} />;
  if (!feed) return null;

  const t = feed.thresholds;
  const stats = feed.stats;
  const received = (stats.filings_in_window ?? filings.length + feed.collapsed_noise.count);

  return (
    <div className="space-y-4">
      {/* status strip */}
      <Card className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 px-3 py-2">
        <div className="min-w-0 text-[11px] text-slate-500">
          <span className="font-semibold text-slate-700">Last {th.window_hours >= 48 && th.window_hours % 24 === 0 ? `${th.window_hours / 24}d` : `${th.window_hours}h`}</span>
          {" · "}&plusmn;{t.price_move_pct}% &middot; &ge;{t.volume_ratio}&times; vol
          {" · "}<span className="tabular-nums">Updated {fmtDT(push.data?.asOf)}</span>
        </div>
        <div className="flex items-center gap-2">
          {push.loading && <Spinner />}
          <SnapshotBadge source={push.data?.source} asOf={push.data?.asOf} onRetry={retryLive} />
          <button onClick={push.reload} className="rounded border border-slate-300 px-2 py-0.5 text-[11px] font-semibold text-slate-700 active:bg-slate-100">Refresh</button>
        </div>
      </Card>

      {/* signal funnel: shows noise-filtering at a glance */}
      <div className="grid grid-cols-3 divide-x divide-slate-200 rounded-md border border-slate-200 bg-white text-center">
        {[
          ["Filings", received, "text-slate-900"],
          ["Material", filings.length, "text-slate-900"],
          ["Noise cut", feed.collapsed_noise.count, "text-slate-400"],
        ].map(([l, v, c]) => (
          <div key={l as string} className="py-2">
            <div className={cx("text-[18px] font-bold leading-none tabular-nums", c as string)}>{v as number}</div>
            <div className="mt-1 text-2xs font-semibold uppercase tracking-wide text-slate-500">{l as string}</div>
          </div>
        ))}
      </div>

      {/* EOD alerts */}
      <section className="space-y-2">
        <SectionLabel right={<span className="text-[11px] tabular-nums text-slate-500">{feed.alerts.length} breach{feed.alerts.length === 1 ? "" : "es"}</span>}>
          EOD market alerts
        </SectionLabel>
        {feed.alerts.length === 0 ? (
          <Card className="px-3 py-4 text-center text-[12px] text-slate-500">
            No stock breached &plusmn;{t.price_move_pct}% or &ge;{t.volume_ratio}&times; 20D volume in the latest session.
          </Card>
        ) : (
          <div className="-mx-3 flex snap-x snap-mandatory gap-2 overflow-x-auto px-3 pb-1 no-scrollbar md:mx-0 md:grid md:grid-cols-2 md:gap-3 md:overflow-visible md:px-0 lg:grid-cols-3">
            {feed.alerts.map((a) => (
              <div key={a.symbol} className="w-[82%] shrink-0 snap-start sm:w-[60%] md:w-auto">
                <AlertCard alert={a} pf={pfBySym.get(a.symbol)} onOpen={openResearch} />
              </div>
            ))}
          </div>
        )}
      </section>

      {/* signal-ranked filings */}
      <section className="space-y-2">
        <SectionLabel right={<span className="text-[11px] text-slate-500">Ranked by signal &times; recency</span>}>Material filings</SectionLabel>
        <div className="-mx-3 flex gap-1.5 overflow-x-auto px-3 no-scrollbar md:mx-0 md:px-0">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              onClick={() => setFilter(f.id)}
              className={cx(
                "shrink-0 rounded border px-2.5 py-1 text-2xs font-bold uppercase tracking-wide",
                filter === f.id ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300 bg-white text-slate-600 active:bg-slate-100",
              )}
            >
              {f.label} <span className="tabular-nums opacity-60">{counts[f.id] ?? 0}</span>
            </button>
          ))}
        </div>
        {filtered.length === 0 ? (
          <Card className="px-3 py-4 text-center text-[12px] text-slate-500">No material filings in this category for the selected window.</Card>
        ) : (
          <div className="grid items-start gap-3 md:grid-cols-2">
            {filtered.map((i) => <FilingCard key={i.filing!.id} item={i} onOpen={openResearch} />)}
          </div>
        )}
        <NoiseDrawer data={feed.collapsed_noise} />
      </section>

      {feed.coverage_notes.length > 0 && (
        <ul className="space-y-1 px-1 text-[11px] text-slate-500">
          {feed.coverage_notes.map((n, i) => <li key={i}>&#9432; {n}</li>)}
        </ul>
      )}
      <p className="px-1 text-center text-[10px] text-slate-400 tabular-nums">
        {stats.llm_calls ?? 0} LLM calls &middot; ${(stats.llm_cost_usd ?? 0).toFixed(4)} to build this feed
        {stats.latency_s != null ? ` · ${stats.latency_s}s` : ""}
      </p>
    </div>
  );
}
