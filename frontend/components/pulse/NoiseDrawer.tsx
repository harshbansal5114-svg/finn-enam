"use client";

import { useState } from "react";
import { Card } from "../ui";
import { ExternalIcon } from "../icons";
import { catMeta } from "@/lib/categories";
import { ago, bare, cx } from "@/lib/format";
import type { NoiseDrawerData } from "@/lib/types";

export default function NoiseDrawer({ data }: { data: NoiseDrawerData }) {
  const [open, setOpen] = useState(false);
  if (!data.count) return null;
  const cats = Object.entries(data.by_category).sort((a, b) => b[1] - a[1]);
  return (
    <Card>
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left active:bg-slate-50">
        <span className="flex items-center gap-2">
          <span aria-hidden className={cx("text-[10px] text-slate-400 transition-transform", open && "rotate-90")}>&#9656;</span>
          <span className="text-[12px] font-semibold text-slate-700">{data.count} routine filings collapsed</span>
        </span>
        <span className="hidden text-[11px] text-slate-500 sm:inline">{cats.slice(0, 3).map(([k, v]) => `${catMeta(k).label.toLowerCase()} ×${v}`).join(" · ")}</span>
      </button>
      {open && (
        <div className="border-t border-slate-100 px-3 py-2">
          <div className="mb-2 flex flex-wrap gap-1.5">
            {cats.map(([k, v]) => (
              <span key={k} className="rounded border border-slate-200 bg-slate-50 px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                {catMeta(k).label} &times;{v}
              </span>
            ))}
          </div>
          <ul className="divide-y divide-slate-100">
            {data.samples.map((s) => (
              <li key={s.id} className="flex items-start justify-between gap-2 py-1.5">
                <div className="min-w-0">
                  <div className="line-clamp-2 text-[12px] text-slate-600">
                    <span className="font-semibold text-slate-800">{bare(s.symbol)}</span> &middot; {s.subject}
                  </div>
                  <div className="text-[10px] text-slate-400">{ago(s.filed_at)}</div>
                </div>
                {s.url && (
                  <a href={s.url} target="_blank" rel="noreferrer" aria-label="Open filing" className="mt-0.5 shrink-0 text-slate-400 active:text-slate-700">
                    <ExternalIcon />
                  </a>
                )}
              </li>
            ))}
          </ul>
          {data.count > data.samples.length && <p className="pt-1.5 text-[10px] text-slate-400">Showing the {data.samples.length} most recent of {data.count}.</p>}
        </div>
      )}
    </Card>
  );
}
