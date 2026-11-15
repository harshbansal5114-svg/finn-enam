"use client";

import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Card, Chip, ErrorBox, SnapshotBadge, Spinner, Skeleton } from "../ui";
import { ExternalIcon, SearchIcon } from "../icons";
import { CiteCtx } from "./Cite";
import NarrativeAudit from "./NarrativeAudit";
import CashQuality from "./CashQuality";
import { Business, BullBearView, DataGaps, GuidanceList, SnapshotMetrics, TrajectoryView } from "./Insights";
import { fetchPull, normalizeTicker } from "@/lib/api";
import { makeResolver } from "@/lib/citations";
import { bare, cx, fmtDT, usd } from "@/lib/format";
import { useElapsed, useResource } from "@/lib/hooks";
import { useApp } from "@/lib/store";
import type { PullReport } from "@/lib/types";

const CHIPS = ["DIXON", "BEL", "TRENT", "TITAN", "PERSISTENT"];

function ReportHeader({ r, source, asOf, onRetry }: { r: PullReport; source?: "live" | "snapshot"; asOf?: string | null; onRetry: () => void }) {
  const mode =
    r.mode === "concall" ? { label: `Concall · ${r.concall_periods.length} transcript${r.concall_periods.length === 1 ? "" : "s"} · ${r.concall_pages} pp`, tone: "blue" as const }
    : r.mode === "annual_report" ? { label: "Annual Report basis", tone: "violet" as const }
    : { label: "Financials only", tone: "slate" as const };
  return (
    <Card className="p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-[18px] font-bold leading-tight text-slate-900">{r.company || bare(r.symbol)}</div>
          <div className="mt-0.5 text-[12px] text-slate-500">{bare(r.symbol)} &middot; NSE &middot; {r.basis}</div>
        </div>
        <SnapshotBadge source={source} asOf={asOf} onRetry={onRetry} />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <Chip tone={mode.tone}>{mode.label}</Chip>
        <Chip tone={r.cache_hit ? "green" : "slate"} title={r.cache_hit ? "Served from the report cache: $0 and instant" : "Freshly generated"}>{r.cache_hit ? "Cache hit" : "Fresh"}</Chip>
        <span className="text-[11px] tabular-nums text-slate-500">
          {fmtDT(r.generated_at)} &middot; {(r.latency_ms / 1000).toFixed(1)}s &middot; {usd(r.llm_cost_usd)} LLM
        </span>
      </div>
      {r.mode === "annual_report" && (
        <p className="mt-2 rounded bg-violet-50 px-2 py-1.5 text-[11.5px] leading-snug text-violet-900">
          No concall transcripts were available, so narrative and guidance are grounded in the latest Annual Report. Every claim below still cites its page.
        </p>
      )}
    </Card>
  );
}

function Sources({ urls }: { urls: string[] }) {
  if (!urls.length) return null;
  return (
    <Card className="p-3">
      <div className="text-2xs font-bold uppercase tracking-[0.08em] text-slate-500">Primary sources read</div>
      <ul className="mt-1.5 space-y-1">
        {urls.map((u) => (
          <li key={u} className="min-w-0">
            <a href={u} target="_blank" rel="noreferrer" className="inline-flex max-w-full items-center gap-1 text-[11.5px] text-blue-700 active:underline">
              <span className="truncate">{u.replace(/^https?:\/\//, "")}</span> <ExternalIcon className="shrink-0" />
            </a>
          </li>
        ))}
      </ul>
      <p className="mt-1.5 text-[10px] text-slate-400">Analysis uses only Screener fundamentals, concall transcripts and Annual Reports: no news, no broker consensus.</p>
    </Card>
  );
}

function Loading({ ticker, elapsed }: { ticker: string; elapsed: number }) {
  return (
    <div className="space-y-3">
      <Card className="p-3">
        <div className="flex items-center gap-2 text-[12.5px] font-semibold text-slate-800"><Spinner /> Auditing {ticker} against primary sources&hellip; <span className="tabular-nums text-slate-400">{elapsed}s</span></div>
        <p className="mt-1 text-[11.5px] text-slate-500">Reading Screener tables, concall transcripts and the Annual Report. A first-time ticker can take about a minute; repeat lookups are cached and instant.</p>
      </Card>
      <Card className="space-y-2 p-3"><Skeleton className="h-14 w-full" /><Skeleton className="h-3 w-2/3" /></Card>
      <Card className="space-y-2 p-3"><Skeleton className="h-3 w-1/3" /><Skeleton className="h-16 w-full" /></Card>
    </div>
  );
}

export default function ResearchTab() {
  const { pendingTicker, epoch, retryLive } = useApp();
  const [input, setInput] = useState("");
  const [ticker, setTicker] = useState<string | null>(null);
  const [formErr, setFormErr] = useState<string | null>(null);

  const go = (raw: string) => {
    try {
      const t = normalizeTicker(raw);
      setFormErr(null);
      setInput(t);
      setTicker(t);
    } catch (e) {
      setFormErr(e instanceof Error ? e.message : "Invalid ticker");
    }
  };
  const submit = (e: FormEvent) => { e.preventDefault(); go(input); };

  // cross-tab deep link (tapping a ticker on Pulse / System)
  useEffect(() => { if (pendingTicker) go(pendingTicker.symbol); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [pendingTicker]);

  const res = useResource<PullReport>(`pull:${ticker}:${epoch}`, (f) => fetchPull(ticker!, f), { area: "research", enabled: !!ticker, keepPrevious: false });
  const elapsed = useElapsed(res.loading);
  const r = res.data?.data;
  const resolver = useMemo(() => (r ? makeResolver(r) : () => ({ href: null, kind: "unknown" as const })), [r]);

  return (
    <div className="space-y-3">
      <form onSubmit={submit} className="space-y-2">
        <div className="flex gap-2">
          <label className="relative flex-1">
            <span className="sr-only">NSE ticker</span>
            <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400"><SearchIcon /></span>
            <input
              value={input}
              onChange={(e) => setInput(e.target.value.toUpperCase())}
              placeholder="Any NSE symbol — e.g. TITAN, M&M"
              autoCapitalize="characters"
              autoCorrect="off"
              spellCheck={false}
              enterKeyHint="search"
              className="h-11 w-full rounded-md border border-slate-300 bg-white pl-8 pr-3 text-[14px] font-semibold uppercase tracking-wide text-slate-900 placeholder:font-normal placeholder:normal-case placeholder:tracking-normal placeholder:text-slate-400 focus:border-slate-900 focus:outline-none focus:ring-1 focus:ring-slate-900"
            />
          </label>
          <button type="submit" disabled={!input.trim()} className="h-11 rounded-md bg-slate-900 px-4 text-[13px] font-bold uppercase tracking-wide text-white active:bg-slate-700 disabled:bg-slate-300">
            Audit
          </button>
        </div>
        <div className="-mx-3 flex gap-1.5 overflow-x-auto px-3 no-scrollbar md:mx-0 md:px-0">
          {CHIPS.map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => go(c)}
              className={cx(
                "shrink-0 rounded border px-3 py-1.5 text-[12px] font-bold tracking-wide",
                ticker === c ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300 bg-white text-slate-700 active:bg-slate-100",
              )}
            >
              {c}
            </button>
          ))}
        </div>
        {formErr && <p className="text-[12px] text-red-700">{formErr}</p>}
      </form>

      {!ticker && (
        <Card className="px-4 py-8 text-center">
          <p className="text-[13px] font-semibold text-slate-800">Primary-source company audit</p>
          <p className="mx-auto mt-1 max-w-sm text-[12px] leading-snug text-slate-500">
            Management&apos;s promises vs. what the numbers delivered, plus cash quality &mdash; grounded only in Screener fundamentals, concall transcripts and Annual Reports.
          </p>
        </Card>
      )}

      {ticker && res.loading && !r && <Loading ticker={ticker} elapsed={elapsed} />}
      {ticker && res.error && !r && <ErrorBox message={res.error} onRetry={res.reload} />}

      {r && (
        <CiteCtx.Provider value={resolver}>
          <div className="space-y-4">
            <ReportHeader r={r} source={res.data?.source} asOf={res.data?.asOf} onRetry={retryLive} />
            {r.narrative_vs_numbers && <NarrativeAudit nvn={r.narrative_vs_numbers} />}
            {r.cash_quality && <CashQuality cq={r.cash_quality} metrics={r.metrics} />}
            {r.snapshot && <SnapshotMetrics s={r.snapshot} metrics={r.metrics} />}
            {r.trajectory && <TrajectoryView t={r.trajectory} />}
            {r.bull_bear && <BullBearView bb={r.bull_bear} />}
            {r.business && <Business b={r.business} />}
            {r.guidance.length > 0 && <GuidanceList items={r.guidance} />}
            <DataGaps r={r} />
            <Sources urls={r.sources_used} />
          </div>
        </CiteCtx.Provider>
      )}
    </div>
  );
}
