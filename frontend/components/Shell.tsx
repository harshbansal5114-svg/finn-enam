"use client";

import { useEffect, useRef } from "react";
import { PulseIcon, ResearchIcon, SystemIcon } from "./icons";
import PulseTab from "./pulse/PulseTab";
import ResearchTab from "./research/ResearchTab";
import SystemTab from "./system/SystemTab";
import { useApp, type Tab } from "@/lib/store";
import { cx } from "@/lib/format";

const NAV: { id: Tab; label: string; Icon: typeof PulseIcon }[] = [
  { id: "pulse", label: "Pulse", Icon: PulseIcon },
  { id: "research", label: "Research", Icon: ResearchIcon },
  { id: "system", label: "System", Icon: SystemIcon },
];

function Header() {
  const { sources, retryLive } = useApp();
  const offline = Object.values(sources).includes("snapshot");
  const live = !offline && Object.values(sources).includes("live");
  return (
    <header className="sticky top-0 z-30 bg-slate-900 pt-[env(safe-area-inset-top)] text-white">
      <div className="mx-auto flex h-11 max-w-5xl items-center justify-between px-3 md:px-6">
        <div className="flex items-baseline gap-2">
          <span className="text-[15px] font-black tracking-[0.18em]">FINN</span>
          <span className="hidden text-2xs uppercase tracking-widest text-slate-400 sm:inline">Investment Agent &middot; NSE</span>
        </div>
        {offline ? (
          <button
            onClick={retryLive}
            title="Backend slow or unreachable — serving the static snapshot. Tap to retry live."
            className="flex items-center gap-1.5 rounded border border-white/20 px-2 py-0.5 text-2xs font-semibold uppercase tracking-wide text-slate-300 active:bg-white/10"
          >
            <span className="h-1.5 w-1.5 rounded-full bg-slate-400" />
            Cached Snapshot
            <span aria-hidden>&#8635;</span>
          </button>
        ) : (
          <span className="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wide text-slate-300">
            <span className={cx("h-1.5 w-1.5 rounded-full", live ? "bg-emerald-400" : "bg-slate-600")} />
            {live ? "Live" : "Connecting"}
          </span>
        )}
      </div>
    </header>
  );
}

function BottomNav() {
  const { tab, setTab } = useApp();
  return (
    <nav aria-label="Primary" className="fixed inset-x-0 bottom-0 z-30 border-t border-slate-200 bg-white/95 pb-[env(safe-area-inset-bottom)] backdrop-blur">
      <div className="mx-auto grid h-14 max-w-5xl grid-cols-3">
        {NAV.map(({ id, label, Icon }) => {
          const active = tab === id;
          return (
            <button
              key={id}
              onClick={() => setTab(id)}
              aria-current={active ? "page" : undefined}
              className={cx(
                "relative flex flex-col items-center justify-center gap-0.5 text-2xs font-bold uppercase tracking-wide transition-colors",
                active ? "text-slate-900" : "text-slate-400 active:text-slate-600",
              )}
            >
              {active && <span className="absolute inset-x-6 top-0 h-0.5 rounded-b bg-slate-900" />}
              <Icon />
              {label}
            </button>
          );
        })}
      </div>
    </nav>
  );
}

export default function Shell() {
  const { tab } = useApp();
  // Mount a tab on first visit, then keep it mounted (hidden) so scroll/search/filters survive tab switches.
  const visited = useRef<Set<Tab>>(new Set());
  visited.current.add(tab);
  useEffect(() => { window.scrollTo({ top: 0 }); }, [tab]);

  return (
    <>
      <Header />
      <main className="pb-nav mx-auto w-full max-w-5xl px-3 pt-3 md:px-6 md:pt-5">
        {visited.current.has("pulse") && <section className={tab === "pulse" ? "" : "hidden"}><PulseTab /></section>}
        {visited.current.has("research") && <section className={tab === "research" ? "" : "hidden"}><ResearchTab /></section>}
        {visited.current.has("system") && <section className={tab === "system" ? "" : "hidden"}><SystemTab /></section>}
      </main>
      <BottomNav />
    </>
  );
}
