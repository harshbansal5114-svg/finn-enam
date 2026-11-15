"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Loaded } from "./api";
import { useApp } from "./store";

interface State<T> { data: Loaded<T> | null; loading: boolean; error: string | null }

/**
 * Tiny SWR-ish hook. `key` change => refetch (and optionally drop previous data).
 * Reports the data source to the app header so one "Cached Snapshot" badge reflects reality.
 */
export function useResource<T>(
  key: string,
  load: (force: boolean) => Promise<Loaded<T>>,
  opts: { area: string; enabled?: boolean; keepPrevious?: boolean } = { area: "misc" },
) {
  const { reportSource } = useApp();
  const { area, enabled = true, keepPrevious = true } = opts;
  const [state, setState] = useState<State<T>>({ data: null, loading: enabled, error: null });
  const seq = useRef(0);
  const loadRef = useRef(load);
  loadRef.current = load;

  const run = useCallback(
    (force: boolean) => {
      const id = ++seq.current;
      setState((s) => ({ ...s, loading: true, error: null }));
      loadRef.current(force)
        .then((d) => {
          if (id !== seq.current) return;
          setState({ data: d, loading: false, error: null });
          reportSource(area, d.source);
        })
        .catch((e: unknown) => {
          if (id !== seq.current) return;
          setState((s) => ({ ...s, loading: false, error: e instanceof Error ? e.message : String(e) }));
        });
    },
    [area, reportSource],
  );

  useEffect(() => {
    if (!enabled) return;
    if (!keepPrevious) setState({ data: null, loading: true, error: null });
    run(false);
    return () => { seq.current++; };
  }, [key, enabled, keepPrevious, run]);

  return { ...state, reload: () => run(true) };
}

export function useDebounced<T>(value: T, ms = 500): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function useElapsed(active: boolean): number {
  const [s, setS] = useState(0);
  useEffect(() => {
    if (!active) { setS(0); return; }
    const t0 = Date.now();
    const i = setInterval(() => setS(Math.floor((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(i);
  }, [active]);
  return s;
}
