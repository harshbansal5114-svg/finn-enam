import { norm } from "./format";
import type { PullReport } from "./types";

/** Splits "text [Tag A] more [Tag B]" into plain / tag parts. */
export function splitTags(text: string): { tag?: string; text?: string }[] {
  const out: { tag?: string; text?: string }[] = [];
  const re = /\[([^[\]]{2,80})\]/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push({ text: text.slice(last, m.index) });
    out.push({ tag: m[1].trim() });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ text: text.slice(last) });
  return out;
}

export const tagsOf = (s: string): string[] => splitTags(s).flatMap((p) => (p.tag ? [p.tag] : []));

const SCREENER_ANCHORS: Record<string, string> = {
  "Screener Quarterly P&L": "#quarters",
  "Screener Annual P&L": "#profit-loss",
  "Screener Balance Sheet": "#balance-sheet",
  "Screener Cash Flow": "#cash-flow",
  "Screener Ratios": "#ratios",
  "Screener About": "",
};

export interface Resolved { href: string | null; kind: "screener" | "concall" | "ar" | "unknown" }

/**
 * Maps a citation tag to its primary source URL.
 * Backend contract: sources_used = [screener_url, ...docs actually read]; docs align with concall_periods
 * (concall mode) or hold the single Annual Report (annual_report mode).
 */
export function makeResolver(r: PullReport): (tag: string) => Resolved {
  const screener = r.sources_used[0] ?? null;
  const docs = r.sources_used.slice(1);
  const periods = r.mode === "concall" ? r.concall_periods : [];
  return (tag) => {
    const t = tag.trim();
    if (t in SCREENER_ANCHORS) return { href: screener ? `${screener}${SCREENER_ANCHORS[t]}` : null, kind: "screener" };
    const m = /^(Concall|AR)\s+(.+?),\s*p\.\s*(\d+)$/i.exec(t);
    if (!m) return { href: null, kind: "unknown" };
    const kind = m[1].toLowerCase() === "ar" ? "ar" : "concall";
    const idx = kind === "ar" ? 0 : periods.findIndex((p) => norm(p) === norm(m[2]));
    const url = idx >= 0 ? docs[idx] : undefined;
    return { href: url ? `${url}#page=${m[3]}` : null, kind };
  };
}
