"use client";

import { Card, SectionLabel } from "../ui";
import { DEFAULT_THRESHOLDS, evaluateAlerts } from "@/lib/api";
import { cx } from "@/lib/format";
import type { PortfolioItem, Thresholds } from "@/lib/types";

const WINDOWS: [number, string][] = [[6, "6h"], [24, "24h"], [72, "3d"], [168, "7d"], [720, "30d"]];

function Slider({ label, value, min, max, step, fmt, hint, onChange }: { label: string; value: number; min: number; max: number; step: number; fmt: (v: number) => string; hint: string; onChange: (v: number) => void }) {
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <label className="text-[12px] font-semibold text-slate-800">{label}</label>
        <span className="text-[15px] font-bold tabular-nums text-slate-900">{fmt(value)}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} aria-label={label} />
      <div className="flex justify-between text-[10px] tabular-nums text-slate-400"><span>{fmt(min)}</span><span>{hint}</span><span>{fmt(max)}</span></div>
    </div>
  );
}

export default function ThresholdControls({ th, onChange, onReset, items }: { th: Thresholds; onChange: (t: Thresholds) => void; onReset: () => void; items: PortfolioItem[] }) {
  const breaches = evaluateAlerts(items, th).length;
  const dirty = th.window_hours !== DEFAULT_THRESHOLDS.window_hours || th.price_thresh !== DEFAULT_THRESHOLDS.price_thresh || th.vol_mult !== DEFAULT_THRESHOLDS.vol_mult;
  return (
    <section className="space-y-2">
      <SectionLabel
        right={dirty ? <button onClick={onReset} className="text-[11px] font-semibold text-blue-700 active:underline">Reset defaults</button> : <span className="text-[11px] text-slate-400">Defaults</span>}
      >
        Push alert thresholds
      </SectionLabel>
      <Card className="space-y-4 p-3">
        <div>
          <div className="mb-1.5 flex items-baseline justify-between">
            <span className="text-[12px] font-semibold text-slate-800">Filings window</span>
            <span className="text-[15px] font-bold tabular-nums text-slate-900">{th.window_hours}h</span>
          </div>
          <div className="flex gap-1.5">
            {WINDOWS.map(([h, l]) => (
              <button key={h} onClick={() => onChange({ ...th, window_hours: h })} className={cx("flex-1 rounded border py-1.5 text-[12px] font-bold", th.window_hours === h ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300 bg-white text-slate-700 active:bg-slate-100")}>
                {l}
              </button>
            ))}
          </div>
        </div>
        <Slider label="Price move trigger (±)" value={th.price_thresh} min={1} max={15} step={0.5} fmt={(v) => `${v.toFixed(1)}%`} hint="|1D return|" onChange={(v) => onChange({ ...th, price_thresh: v })} />
        <Slider label="Volume spike trigger" value={th.vol_mult} min={1} max={5} step={0.1} fmt={(v) => `${v.toFixed(1)}×`} hint="vs 20D average" onChange={(v) => onChange({ ...th, vol_mult: v })} />
        <div className="flex items-center justify-between rounded bg-slate-50 px-2.5 py-2 text-[12px]">
          <span className="text-slate-600">Live preview on latest session</span>
          <span className="font-bold tabular-nums text-slate-900">{breaches} of {items.length} breach</span>
        </div>
        <p className="text-[10px] text-slate-400">Changes apply to the Pulse feed automatically (debounced) and persist on this device.</p>
      </Card>
    </section>
  );
}
