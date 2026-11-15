export const cx = (...a: (string | false | null | undefined)[]) => a.filter(Boolean).join(" ");

export const bare = (s: string) => s.replace(/\.NS$/i, "");

const MINUS = "−";

export function num(v: number | null | undefined, d = 0): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
}

export function pct(v: number | null | undefined, d = 1, signed = true): string {
  if (v == null || Number.isNaN(v)) return "—";
  const body = Math.abs(v).toFixed(d);
  return `${v < 0 ? MINUS : signed && v > 0 ? "+" : ""}${body}%`;
}

export const inr = (v: number | null | undefined, d = 2) => (v == null ? "—" : `₹${num(v, d)}`);

export const mult = (v: number | null | undefined, d = 2) => (v == null ? "—" : `${v.toFixed(d)}×`);

export function usd(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v === 0) return "$0.00";
  return `$${v < 0.01 ? v.toFixed(5) : v < 1 ? v.toFixed(4) : v.toFixed(2)}`;
}

export const pctTone = (v: number | null | undefined) =>
  v == null ? "text-slate-400" : v > 0 ? "text-emerald-700" : v < 0 ? "text-red-700" : "text-slate-500";

/** Backend datetimes are IST-aware ISO strings; treat naive ones as IST. */
export function parseDate(s: string): Date {
  const hasTz = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(s);
  return new Date(s.includes("T") && !hasTz ? `${s}+05:30` : s);
}

const dtFmt = new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
const dFmt = new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "Asia/Kolkata" });

export function fmtDT(s: string | null | undefined): string {
  if (!s) return "—";
  const d = parseDate(s);
  return Number.isNaN(d.getTime()) ? s : `${dtFmt.format(d)} IST`;
}
export function fmtD(s: string | null | undefined): string {
  if (!s) return "—";
  const d = parseDate(s);
  return Number.isNaN(d.getTime()) ? s : dFmt.format(d);
}

export function ago(s: string): string {
  const d = parseDate(s).getTime();
  if (Number.isNaN(d)) return "";
  const m = Math.max(0, Math.round((Date.now() - d) / 60000));
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

/** "Mar 2025" -> "Mar 25" for dense table headers. */
export const shortPeriod = (p: string) => p.replace(/^([A-Za-z]{3})[A-Za-z]*\s+(\d{2})(\d{2})$/, "$1 $3");

export const norm = (s: string) => s.toLowerCase().replace(/\s+/g, " ").trim();
