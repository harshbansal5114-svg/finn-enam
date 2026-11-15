export type Tone = "slate" | "ink" | "green" | "red" | "amber" | "blue" | "violet";

export const CATEGORY_META: Record<string, { label: string; tone: Tone }> = {
  results: { label: "RESULTS", tone: "blue" },
  "M&A": { label: "M&A", tone: "violet" },
  management_change: { label: "MGMT", tone: "amber" },
  capex: { label: "CAPEX", tone: "green" },
  litigation: { label: "LITIGATION", tone: "red" },
  fund_raise: { label: "FUNDRAISE", tone: "slate" },
  credit_rating: { label: "RATING", tone: "slate" },
  pledging: { label: "PLEDGE", tone: "red" },
  dividend: { label: "DIVIDEND", tone: "slate" },
  bonus_split: { label: "BONUS/SPLIT", tone: "slate" },
  insider_trading: { label: "INSIDER", tone: "slate" },
  press_release: { label: "PRESS", tone: "slate" },
  earnings_call: { label: "CALL", tone: "slate" },
  investor_meetings: { label: "MEETING", tone: "slate" },
  agm_egm: { label: "AGM", tone: "slate" },
  postal_ballot: { label: "BALLOT", tone: "slate" },
  unclassified: { label: "OTHER", tone: "slate" },
};

export const catMeta = (c: string) => CATEGORY_META[c] ?? { label: c.toUpperCase(), tone: "slate" as Tone };

/** Filter chips on the Pulse feed (everything not listed falls under OTHER). */
export const FILTERS: { id: string; label: string; cats: string[] | null }[] = [
  { id: "all", label: "ALL", cats: null },
  { id: "results", label: "RESULTS", cats: ["results"] },
  { id: "ma", label: "M&A", cats: ["M&A"] },
  { id: "mgmt", label: "MGMT", cats: ["management_change"] },
  { id: "capex", label: "CAPEX", cats: ["capex"] },
];
