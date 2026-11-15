"use client";

import type { ReactNode } from "react";
import type { Tone } from "@/lib/categories";
import { cx, fmtDT, pct, pctTone } from "@/lib/format";
import type { Source } from "@/lib/api";
import { WarnIcon } from "./icons";

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx("rounded-md border border-slate-200 bg-white", className)}>{children}</div>;
}

export function SectionLabel({ children, right, className }: { children: ReactNode; right?: ReactNode; className?: string }) {
  return (
    <div className={cx("flex items-center justify-between gap-2", className)}>
      <h2 className="text-2xs font-bold uppercase tracking-[0.08em] text-slate-500">{children}</h2>
      {right}
    </div>
  );
}

const TONES: Record<Tone, string> = {
  slate: "bg-slate-100 text-slate-600 border-slate-200",
  ink: "bg-slate-900 text-white border-slate-900",
  green: "bg-emerald-50 text-emerald-800 border-emerald-200",
  red: "bg-red-50 text-red-800 border-red-200",
  amber: "bg-amber-50 text-amber-800 border-amber-300",
  blue: "bg-blue-50 text-blue-800 border-blue-200",
  violet: "bg-violet-50 text-violet-800 border-violet-200",
};

export function Chip({ tone = "slate", children, title, className }: { tone?: Tone; children: ReactNode; title?: string; className?: string }) {
  return (
    <span title={title} className={cx("inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-px text-2xs font-bold uppercase tracking-wide", TONES[tone], className)}>
      {children}
    </span>
  );
}

export function Pct({ v, d = 1, className }: { v: number | null | undefined; d?: number; className?: string }) {
  return <span className={cx("tabular-nums", pctTone(v), className)}>{pct(v, d)}</span>;
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("animate-pulse rounded bg-slate-200/80", className)} />;
}

export function CardSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <Card className="space-y-2 p-3">
      <Skeleton className="h-3 w-24" />
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className={cx("h-4", i % 2 ? "w-3/4" : "w-full")} />
      ))}
    </Card>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <Card className="border-red-200 bg-red-50 p-3">
      <p className="text-[12px] font-semibold text-red-800">Couldn&apos;t load data</p>
      <p className="mt-0.5 break-words text-[12px] text-red-700">{message}</p>
      {onRetry && (
        <button onClick={onRetry} className="mt-2 rounded border border-red-300 bg-white px-2.5 py-1 text-[12px] font-semibold text-red-800 active:bg-red-100">
          Retry
        </button>
      )}
    </Card>
  );
}

/** Subtle marker shown on any view that is currently served from the static bundle. */
export function SnapshotBadge({ source, asOf, onRetry }: { source: Source | undefined; asOf?: string | null; onRetry?: () => void }) {
  if (source !== "snapshot") return null;
  return (
    <button
      onClick={onRetry}
      title={`Backend unreachable or slow — showing the static snapshot${asOf ? ` generated ${fmtDT(asOf)}` : ""}. Tap to retry live.`}
      className="inline-flex items-center gap-1 rounded border border-slate-300 bg-slate-50 px-1.5 py-px text-2xs font-semibold uppercase tracking-wide text-slate-500 active:bg-slate-200"
    >
      <span className="h-1.5 w-1.5 rounded-full bg-slate-400" />
      Cached Snapshot
      {onRetry && <span aria-hidden className="text-slate-400">&#8635;</span>}
    </button>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <span className={cx("inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-slate-300 border-t-slate-800", className)} aria-label="Loading" />;
}

export function Collapsible({ title, summary, children, defaultOpen = false }: { title: ReactNode; summary?: ReactNode; children: ReactNode; defaultOpen?: boolean }) {
  return (
    <details open={defaultOpen} className="group rounded-md border border-slate-200 bg-white">
      <summary className="flex cursor-pointer select-none items-center justify-between gap-2 px-3 py-2.5 active:bg-slate-50">
        <span className="flex min-w-0 items-center gap-2">
          <span aria-hidden className="text-[10px] text-slate-400 transition-transform group-open:rotate-90">&#9656;</span>
          <span className="truncate text-[12px] font-semibold text-slate-700">{title}</span>
        </span>
        {summary && <span className="shrink-0 text-[11px] text-slate-500">{summary}</span>}
      </summary>
      <div className="border-t border-slate-100 p-3">{children}</div>
    </details>
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: string }) {
  return (
    <div className="min-w-0">
      <div className="truncate text-2xs font-semibold uppercase tracking-wide text-slate-500">{label}</div>
      <div className={cx("mt-0.5 truncate text-[15px] font-semibold tabular-nums leading-tight text-slate-900", tone)}>{value}</div>
      {sub && <div className="mt-0.5 truncate text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

export function WarnLine({ children }: { children: ReactNode }) {
  return (
    <li className="flex items-start gap-1.5 text-[12px] text-amber-900">
      <WarnIcon className="mt-0.5 shrink-0 text-amber-700" />
      <span>{children}</span>
    </li>
  );
}
