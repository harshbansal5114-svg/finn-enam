"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { DEFAULT_THRESHOLDS, type Source } from "./api";
import type { Thresholds } from "./types";

export type Tab = "pulse" | "research" | "system";
const TABS: Tab[] = ["pulse", "research", "system"];
const LS_KEY = "finn.thresholds.v1";

interface Ctx {
  tab: Tab;
  setTab: (t: Tab) => void;
  thresholds: Thresholds;
  setThresholds: (t: Thresholds) => void;
  resetThresholds: () => void;
  /** Last data source reported by each area; header shows "Cached Snapshot" if any is a snapshot. */
  sources: Record<string, Source | undefined>;
  reportSource: (area: string, s: Source) => void;
  /** Bumping the epoch makes every resource refetch live (used by the "retry live" action). */
  epoch: number;
  retryLive: () => void;
  /** Cross-tab request: open Research on a ticker (nonce lets the same ticker be re-opened). */
  pendingTicker: { symbol: string; n: number } | null;
  openResearch: (symbol: string) => void;
}

const AppCtx = createContext<Ctx | null>(null);
export const useApp = () => {
  const c = useContext(AppCtx);
  if (!c) throw new Error("useApp outside AppProvider");
  return c;
};

export function AppProvider({ children }: { children: ReactNode }) {
  const [tab, setTabState] = useState<Tab>("pulse");
  const [thresholds, setThresholdsState] = useState<Thresholds>(DEFAULT_THRESHOLDS);
  const [sources, setSources] = useState<Record<string, Source | undefined>>({});
  const [epoch, setEpoch] = useState(0);
  const [pendingTicker, setPending] = useState<Ctx["pendingTicker"]>(null);

  // hydrate from URL hash + localStorage after mount (avoids SSR mismatch)
  useEffect(() => {
    const h = window.location.hash.replace("#", "") as Tab;
    if (TABS.includes(h)) setTabState(h);
    try {
      const raw = localStorage.getItem(LS_KEY);
      if (raw) setThresholdsState({ ...DEFAULT_THRESHOLDS, ...(JSON.parse(raw) as Partial<Thresholds>) });
    } catch { /* ignore corrupt storage */ }
  }, []);

  const setTab = useCallback((t: Tab) => {
    setTabState(t);
    try { window.history.replaceState(null, "", `#${t}`); } catch { /* noop */ }
    window.scrollTo({ top: 0 });
  }, []);

  const setThresholds = useCallback((t: Thresholds) => {
    setThresholdsState(t);
    try { localStorage.setItem(LS_KEY, JSON.stringify(t)); } catch { /* noop */ }
  }, []);

  const value = useMemo<Ctx>(
    () => ({
      tab, setTab, thresholds, setThresholds,
      resetThresholds: () => setThresholds(DEFAULT_THRESHOLDS),
      sources,
      reportSource: (area, s) => setSources((p) => (p[area] === s ? p : { ...p, [area]: s })),
      epoch,
      retryLive: () => setEpoch((e) => e + 1),
      pendingTicker,
      openResearch: (symbol) => {
        setPending((p) => ({ symbol, n: (p?.n ?? 0) + 1 }));
        setTab("research");
      },
    }),
    [tab, setTab, thresholds, setThresholds, sources, epoch, pendingTicker],
  );

  return <AppCtx.Provider value={value}>{children}</AppCtx.Provider>;
}
