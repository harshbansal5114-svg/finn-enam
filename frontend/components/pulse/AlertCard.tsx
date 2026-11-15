"use client";

import { Chip, Card, Pct } from "../ui";
import { bare, cx, fmtD, inr, mult, pct } from "@/lib/format";
import type { AlertTrigger, EODAlert, PortfolioItem } from "@/lib/types";
import type { Tone } from "@/lib/categories";

const TRIG: Record<AlertTrigger, (a: EODAlert) => { label: string; tone: Tone }> = {
  price_up: (a) => ({ label: `▲ Price ≥ +${a.thresholds.price_move_pct}%`, tone: "green" }),
  price_down: (a) => ({ label: `▼ Price ≤ −${a.thresholds.price_move_pct}%`, tone: "red" }),
  volume_spike: (a) => ({ label: `Vol ≥ ${a.thresholds.volume_ratio}× 20D`, tone: "amber" }),
  near_52w_high: () => ({ label: "Near 52W high", tone: "blue" }),
  near_52w_low: () => ({ label: "Near 52W low", tone: "violet" }),
};

/** 52W low/high are derived from the close and the backend's distance fields (dist_high ≤ 0 ≤ dist_low). */
function range52(close: number, p: PortfolioItem) {
  const hi = close / (1 + p.dist_from_52w_high_pct / 100);
  const lo = close / (1 + p.dist_from_52w_low_pct / 100);
  const pos = hi > lo ? Math.min(100, Math.max(0, ((close - lo) / (hi - lo)) * 100)) : 50;
  return { hi, lo, pos };
}

export default function AlertCard({ alert, pf, onOpen }: { alert: EODAlert; pf?: PortfolioItem; onOpen: (t: string) => void }) {
  const up = alert.price_move_pct >= 0;
  const r = pf && pf.close ? range52(alert.close, pf) : null;
  return (
    <Card className={cx("border-l-4 p-3", up ? "border-l-emerald-600" : "border-l-red-600")}>
      <div className="flex items-start justify-between gap-2">
        <button onClick={() => onOpen(bare(alert.symbol))} className="min-w-0 text-left">
          <div className="text-[15px] font-bold leading-tight text-slate-900">{bare(alert.symbol)}</div>
          <div className="truncate text-[11px] text-slate-500">{pf?.name ?? "NSE"}{pf ? ` · ${pf.sector}` : ""}</div>
        </button>
        <div className="text-right">
          <div className="text-[15px] font-semibold tabular-nums text-slate-900">{inr(alert.close)}</div>
          <div className="text-[10px] text-slate-400">{fmtD(alert.as_of)}</div>
        </div>
      </div>

      <div className="mt-2 grid grid-cols-2 gap-3 border-y border-slate-100 py-2">
        <div>
          <div className="text-2xs font-semibold uppercase tracking-wide text-slate-500">1D move</div>
          <Pct v={alert.price_move_pct} className="text-[20px] font-bold leading-tight" />
        </div>
        <div>
          <div className="text-2xs font-semibold uppercase tracking-wide text-slate-500">Volume / 20D avg</div>
          <div className={cx("text-[20px] font-bold leading-tight tabular-nums", alert.volume_ratio >= alert.thresholds.volume_ratio ? "text-amber-700" : "text-slate-900")}>
            {mult(alert.volume_ratio, 1)}
          </div>
        </div>
      </div>

      <div className="mt-2 flex flex-wrap gap-1">
        {alert.triggers.map((t) => {
          const m = TRIG[t](alert);
          return <Chip key={t} tone={m.tone}>{m.label}</Chip>;
        })}
      </div>

      {r && pf && (
        <div className="mt-2.5">
          <div className="relative h-1 rounded-full bg-slate-200">
            <span className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white bg-slate-900 shadow" style={{ left: `${r.pos}%` }} />
          </div>
          <div className="mt-1 flex justify-between text-[10px] tabular-nums text-slate-500">
            <span>52W L {inr(r.lo, 0)}</span>
            <span>52W H {inr(r.hi, 0)}</span>
          </div>
          <div className="mt-0.5 text-[11px] tabular-nums text-slate-600">
            {pct(pf.dist_from_52w_high_pct)} from 52W high &middot; {pct(pf.dist_from_52w_low_pct)} above 52W low
          </div>
        </div>
      )}
    </Card>
  );
}
