"use client";

import { Card, Chip } from "../ui";
import { ExternalIcon } from "../icons";
import ResultsTable from "./ResultsTable";
import MgmtCard from "./MgmtCard";
import { catMeta } from "@/lib/categories";
import { ago, bare, cx, fmtDT } from "@/lib/format";
import type { FeedItem } from "@/lib/types";

function ScoreBadge({ s }: { s: number }) {
  const tone = s >= 8 ? "bg-slate-900 text-white" : s >= 6 ? "bg-slate-600 text-white" : "bg-slate-200 text-slate-700";
  return (
    <span title="Signal score (1–10)" className={cx("rounded px-1.5 py-0.5 text-[11px] font-bold tabular-nums", tone)}>
      {s}/10
    </span>
  );
}

function SourceBadge({ source, demo }: { source: string; demo: boolean }) {
  if (demo || source === "fallback_seed") return <Chip tone="amber" title="Synthetic seed data used because NSE was unreachable">Demo</Chip>;
  if (source === "cache") return <Chip tone="slate" title="Served from the local filings cache">Cached</Chip>;
  return (
    <Chip tone="green" title="Fetched live from NSE corporate announcements">
      <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />Live NSE
    </Chip>
  );
}

export default function FilingCard({ item, onOpen }: { item: FeedItem; onOpen: (t: string) => void }) {
  const f = item.filing;
  if (!f) return null;
  const cat = catMeta(f.category);
  const res = f.category === "results" ? f.enrichment?.results : undefined;
  const mgmt = f.category === "management_change" ? f.enrichment?.management : undefined;
  return (
    <Card className="p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <Chip tone={cat.tone}>{cat.label}</Chip>
          <button onClick={() => onOpen(bare(f.symbol))} className="truncate text-[13px] font-bold text-slate-900 active:underline">
            {bare(f.symbol)}
          </button>
          {f.company && <span className="hidden truncate text-[11px] text-slate-500 sm:inline">{f.company}</span>}
        </div>
        <ScoreBadge s={f.signal_score} />
      </div>

      <p className="mt-1.5 text-[13px] font-medium leading-snug text-slate-900">{item.headline}</p>

      <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-slate-500">
        <span title={fmtDT(f.filed_at)} className="tabular-nums">{fmtDT(f.filed_at)} &middot; {ago(f.filed_at)}</span>
        <SourceBadge source={f.source} demo={item.demo} />
        {item.backfilled && <Chip tone="slate" title="Older than the selected window; surfaced from the 30-day lookback so rich cards are always available">30D lookback</Chip>}
        <span className="text-[10px] uppercase tracking-wide text-slate-400">{f.classified_by === "llm" ? "LLM-tagged" : "Rule-tagged"}</span>
        {f.attachment_url && (
          <a href={f.attachment_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-semibold text-blue-700 active:underline">
            Filing <ExternalIcon />
          </a>
        )}
      </div>

      {res && <ResultsTable r={res} />}
      {mgmt && <MgmtCard m={mgmt} />}
    </Card>
  );
}
