"use client";

import { createContext, useContext } from "react";
import { splitTags, tagsOf, type Resolved } from "@/lib/citations";
import { cx } from "@/lib/format";
import { ExternalIcon, WarnIcon } from "../icons";

export const CiteCtx = createContext<(tag: string) => Resolved>(() => ({ href: null, kind: "unknown" }));

const KIND: Record<Resolved["kind"], string> = {
  screener: "border-slate-300 bg-slate-50 text-slate-600",
  concall: "border-blue-200 bg-blue-50 text-blue-800",
  ar: "border-violet-200 bg-violet-50 text-violet-800",
  unknown: "border-slate-200 bg-white text-slate-500",
};

/** A single primary-source citation; links to the exact transcript/AR page or Screener table when resolvable. */
export function CiteBadge({ tag }: { tag: string }) {
  const r = useContext(CiteCtx)(tag);
  const cls = cx("inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-px font-mono text-2xs font-semibold", KIND[r.kind]);
  return r.href ? (
    <a href={r.href} target="_blank" rel="noreferrer" title="Open primary source" className={cx(cls, "active:opacity-70")}>
      [{tag}] <ExternalIcon />
    </a>
  ) : (
    <span className={cls} title="Source reference">[{tag}]</span>
  );
}

/** Row of badges for a citation string like "[Concall Oct 2025, p.3] [Screener Quarterly P&L]". */
export function Cites({ citation, className }: { citation: string; className?: string }) {
  const tags = tagsOf(citation);
  if (!tags.length) return null;
  return (
    <span className={cx("inline-flex flex-wrap items-center gap-1", className)}>
      {tags.map((t, i) => <CiteBadge key={`${t}-${i}`} tag={t} />)}
    </span>
  );
}

/** Text whose inline [tags] render as badges (used for backend commentary that embeds tags). */
export function CiteText({ text, className }: { text: string; className?: string }) {
  return (
    <span className={className}>
      {splitTags(text).map((p, i) => (p.tag ? <span key={i} className="mx-0.5 align-baseline"><CiteBadge tag={p.tag} /></span> : <span key={i}>{p.text}</span>))}
    </span>
  );
}

export function Ungrounded() {
  return (
    <span title="A figure in this text was not found in the cited source facts — verify before relying on it" className="inline-flex items-center gap-0.5 text-amber-700">
      <WarnIcon />
    </span>
  );
}
