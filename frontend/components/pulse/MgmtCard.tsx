"use client";

import { Chip } from "../ui";
import { ExternalIcon } from "../icons";
import type { ManagementChangeBio } from "@/lib/types";
import type { Tone } from "@/lib/categories";

const ACTION: Record<ManagementChangeBio["action"], { label: string; tone: Tone }> = {
  appointed: { label: "Appointed", tone: "green" },
  resigned: { label: "Resigned", tone: "red" },
  retired: { label: "Retired", tone: "amber" },
  other: { label: "Change", tone: "slate" },
};

export default function MgmtCard({ m }: { m: ManagementChangeBio }) {
  const a = ACTION[m.action] ?? ACTION.other;
  return (
    <div className="mt-2.5 rounded border border-slate-200 bg-slate-50/60 p-2.5">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-2xs font-bold uppercase tracking-[0.08em] text-slate-500">Executive profile</div>
          <div className="mt-0.5 text-[14px] font-semibold leading-tight text-slate-900">{m.person ?? "Person not named in filing"}</div>
          <div className="text-[12px] text-slate-600">
            {m.role ?? "Role not stated"}
            {m.effective_date ? ` · eff. ${m.effective_date}` : ""}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <Chip tone={a.tone}>{a.label}</Chip>
          <Chip tone={m.verified ? "green" : "slate"} title={m.verified ? "Bio bullets are grounded in public sources found for this person" : "No independent source verified"}>
            {m.verified ? "Verified" : "Unverified"}
          </Chip>
        </div>
      </div>
      {m.bullets.length > 0 && (
        <ul className="mt-2 space-y-1">
          {m.bullets.map((b, i) => (
            <li key={i} className="flex gap-1.5 text-[12px] text-slate-700">
              <span aria-hidden className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-slate-400" />
              <span>{b}</span>
            </li>
          ))}
        </ul>
      )}
      {(m.note || m.profile_url) && (
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-500">
          {m.note && <span className="italic">{m.note}</span>}
          {m.profile_url && (
            <a href={m.profile_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-semibold text-blue-700 active:underline">
              {m.profile_url.includes("linkedin.com") ? "LinkedIn" : "Source"} <ExternalIcon />
            </a>
          )}
        </div>
      )}
    </div>
  );
}
