import type {
  AlertTrigger, Bundle, EODAlert, PortfolioItem, PortfolioPayload, PullReport, PushFeed, Telemetry, Thresholds,
} from "./types";

/**
 * Data layer. Design (and why):
 *  - Live-first: every call hits NEXT_PUBLIC_API_URL with a per-endpoint timeout.
 *  - Zero-downtime: on timeout / network error / 5xx we serve the static bundle (/fallback_bundle.json)
 *    and tag the result `source: "snapshot"` so the UI can show a subtle "Cached Snapshot" badge.
 *  - 4xx (bad ticker, no fundamentals) is a *real* answer, never masked by the snapshot.
 *  - Only live results are memoised, so a snapshot never "sticks" once the backend recovers.
 */
export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/+$/, "");
export const BUNDLE_URL = "/fallback_bundle.json";

export type Source = "live" | "snapshot";
export interface Loaded<T> { data: T; source: Source; asOf: string | null }

export const DEFAULT_THRESHOLDS: Thresholds = { window_hours: 24, price_thresh: 5, vol_mult: 2 };

const TIMEOUT_MS = { portfolio: 10_000, push: 25_000, pull: 60_000, telemetry: 8_000 } as const;
const TTL_MS = { portfolio: 60_000, push: 60_000, pull: 10 * 60_000 } as const;

export class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}
const isDefinitive = (e: unknown) => e instanceof HttpError && e.status >= 400 && e.status < 500;

async function getJSON<T>(path: string, timeoutMs: number): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_URL}${path}`, { signal: ctrl.signal, headers: { Accept: "application/json" }, cache: "no-store" });
    if (!res.ok) {
      let detail: unknown = res.statusText;
      try { detail = ((await res.json()) as { detail?: unknown }).detail ?? detail; } catch { /* non-JSON error body */ }
      throw new HttpError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return (await res.json()) as T;
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw new Error(`Backend timed out after ${Math.round(timeoutMs / 1000)}s`);
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

// ───────────── static bundle ─────────────
let bundleP: Promise<Bundle | null> | null = null;
export function loadBundle(): Promise<Bundle | null> {
  if (!bundleP) {
    bundleP = fetch(BUNDLE_URL, { cache: "no-store" })
      .then((r) => (r.ok ? (r.json() as Promise<Bundle>) : null))
      .catch(() => null)
      .then((b) => {
        if (!b) bundleP = null; // allow a later retry (e.g. file added while dev server runs)
        return b;
      });
  }
  return bundleP;
}

// ───────────── memo + in-flight de-dupe ─────────────
const memo = new Map<string, { at: number; value: Loaded<unknown> }>();
const inflight = new Map<string, Promise<Loaded<unknown>>>();

function memoized<T>(key: string, ttlMs: number, force: boolean, run: () => Promise<Loaded<T>>): Promise<Loaded<T>> {
  const hit = memo.get(key);
  if (!force && hit && Date.now() - hit.at < ttlMs) return Promise.resolve(hit.value as Loaded<T>);
  const pending = inflight.get(key);
  if (pending && !force) return pending as Promise<Loaded<T>>;
  const p = run()
    .then((v) => {
      if (v.source === "live") memo.set(key, { at: Date.now(), value: v });
      return v;
    })
    .finally(() => inflight.delete(key));
  inflight.set(key, p);
  return p;
}

async function withFallback<T>(
  live: () => Promise<T>,
  snapshot: (b: Bundle) => { data: T; asOf: string } | null,
  noSnapshotMsg: string | ((b: Bundle | null) => string),
): Promise<Loaded<T>> {
  try {
    return { data: await live(), source: "live", asOf: new Date().toISOString() };
  } catch (e) {
    if (isDefinitive(e)) throw e;
    const b = await loadBundle();
    const s = b ? snapshot(b) : null;
    if (!s) {
      const extra = typeof noSnapshotMsg === "function" ? noSnapshotMsg(b) : noSnapshotMsg;
      throw new Error(`${e instanceof Error ? e.message : "Backend unreachable"}. ${extra}`);
    }
    return { data: s.data, source: "snapshot", asOf: s.asOf };
  }
}

// ───────────── alert evaluation (mirrors schemas.EODAlert.evaluate) ─────────────
export function evaluateAlerts(items: PortfolioItem[], th: Pick<Thresholds, "price_thresh" | "vol_mult">): EODAlert[] {
  const out: EODAlert[] = [];
  for (const s of items) {
    if (s.close == null || s.return_1d_pct == null || s.volume_ratio == null) continue;
    const triggers: AlertTrigger[] = [];
    if (s.return_1d_pct >= th.price_thresh) triggers.push("price_up");
    if (s.return_1d_pct <= -th.price_thresh) triggers.push("price_down");
    if (s.volume_ratio >= th.vol_mult) triggers.push("volume_spike");
    if (!triggers.length) continue;
    out.push({
      symbol: s.symbol,
      as_of: s.as_of ?? "",
      triggers,
      price_move_pct: s.return_1d_pct,
      volume_ratio: s.volume_ratio,
      close: s.close,
      thresholds: { price_move_pct: th.price_thresh, volume_ratio: th.vol_mult, near_52w_pct: 0 },
      headline: `${s.base} ${s.return_1d_pct >= 0 ? "+" : ""}${s.return_1d_pct.toFixed(1)}% on ${s.volume_ratio.toFixed(1)}× volume`,
    });
  }
  return out.sort((a, b) => Math.abs(b.price_move_pct) + b.volume_ratio - (Math.abs(a.price_move_pct) + a.volume_ratio));
}

// ───────────── endpoints ─────────────
export const fetchPortfolio = (force = false) =>
  memoized<PortfolioPayload>("portfolio", TTL_MS.portfolio, force, () =>
    withFallback(
      () => getJSON<PortfolioPayload>("/api/portfolio", TIMEOUT_MS.portfolio),
      (b) => (b.portfolio ? { data: b.portfolio, asOf: b.generated_at } : null),
      "No portfolio in the cached snapshot.",
    ),
  );

export const fetchPush = (th: Thresholds, force = false) =>
  memoized<PushFeed>(`push:${th.window_hours}:${th.price_thresh}:${th.vol_mult}`, TTL_MS.push, force, () =>
    withFallback(
      () => {
        const q = new URLSearchParams({ window_hours: String(th.window_hours), price_thresh: String(th.price_thresh), vol_mult: String(th.vol_mult) });
        return getJSON<PushFeed>(`/api/push?${q}`, TIMEOUT_MS.push);
      },
      (b) => {
        if (!b.push) return null;
        // The snapshot feed was built at default thresholds; alerts are cheap to re-derive from the snapshot
        // portfolio so the threshold controls keep working offline. Filings stay as snapshotted.
        const alerts = b.portfolio ? evaluateAlerts(b.portfolio.items, th) : b.push.alerts;
        return { data: { ...b.push, alerts }, asOf: b.generated_at };
      },
      "No Pulse feed in the cached snapshot.",
    ),
  );

export function normalizeTicker(raw: string): string {
  const t = raw.trim().toUpperCase().replace(/^NSE:/, "").replace(/\.NS$/, "");
  if (!/^[A-Z0-9][A-Z0-9&-]{0,19}$/.test(t)) throw new Error(`"${raw.trim()}" is not a valid NSE symbol (e.g. DIXON, M&M, BAJAJ-AUTO).`);
  return t;
}

export const fetchPull = (ticker: string, force = false) => {
  const t = normalizeTicker(ticker);
  return memoized<PullReport>(`pull:${t}`, TTL_MS.pull, force, () =>
    withFallback(
      () => getJSON<PullReport>(`/api/pull/${encodeURIComponent(t)}`, TIMEOUT_MS.pull),
      (b) => (b.pulls?.[t] ? { data: { ...b.pulls[t], cache_hit: true }, asOf: b.generated_at } : null),
      (b) => {
        const keys = Object.keys(b?.pulls ?? {});
        return b ? `${t} is not in the cached snapshot${keys.length ? ` (available offline: ${keys.join(", ")})` : ""}.` : "No cached snapshot is available either.";
      },
    ),
  );
};

// Telemetry is live-only (a stale ledger would be misleading); the UI renders an "offline" state instead.
export const fetchTelemetry = async (): Promise<Loaded<Telemetry>> => ({
  data: await getJSON<Telemetry>("/api/telemetry", TIMEOUT_MS.telemetry),
  source: "live",
  asOf: new Date().toISOString(),
});

/** Cached-snapshot metadata for the badge tooltip. */
export async function snapshotInfo(): Promise<{ generated_at: string; tickers: string[] } | null> {
  const b = await loadBundle();
  return b ? { generated_at: b.generated_at, tickers: Object.keys(b.pulls ?? {}) } : null;
}
