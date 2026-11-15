"use client";

import Portfolio from "./Portfolio";
import ThresholdControls from "./Thresholds";
import TelemetryPanel from "./Telemetry";
import { CardSkeleton, ErrorBox, SnapshotBadge } from "../ui";
import { fetchPortfolio } from "@/lib/api";
import { useResource } from "@/lib/hooks";
import { useApp } from "@/lib/store";
import type { PortfolioPayload } from "@/lib/types";

export default function SystemTab() {
  const { thresholds, setThresholds, resetThresholds, epoch, openResearch, retryLive } = useApp();
  const port = useResource<PortfolioPayload>(`port:${epoch}`, (f) => fetchPortfolio(f), { area: "portfolio" });
  const p = port.data?.data;

  return (
    <div className="space-y-4">
      <div className="grid items-start gap-4 lg:grid-cols-2">
        <div className="space-y-4">
          {port.loading && !p && <CardSkeleton rows={8} />}
          {port.error && !p && <ErrorBox message={port.error} onRetry={port.reload} />}
          {p && (
            <>
              <div className="flex justify-end empty:hidden"><SnapshotBadge source={port.data?.source} asOf={port.data?.asOf} onRetry={retryLive} /></div>
              <Portfolio items={p.items} asOf={p.as_of} ok={p.ok} th={thresholds} onOpen={openResearch} stale={port.data?.source === "snapshot"} />
            </>
          )}
        </div>
        <div className="space-y-4">
          <ThresholdControls th={thresholds} onChange={setThresholds} onReset={resetThresholds} items={p?.items ?? []} />
          <TelemetryPanel />
        </div>
      </div>
    </div>
  );
}
