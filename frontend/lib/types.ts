// Mirrors backend/schemas.py + the JSON shapes assembled in main.py / push_engine.py.
// Datetimes arrive as ISO strings; Optional[...] fields arrive as `null`.

export type Category =
  | "results" | "M&A" | "fund_raise" | "management_change" | "litigation" | "insider_trading"
  | "earnings_call" | "investor_meetings" | "credit_rating" | "dividend" | "bonus_split" | "capex"
  | "press_release" | "postal_ballot" | "agm_egm" | "pledging" | "unclassified";

// ───────────── Portfolio ─────────────
export interface PortfolioItem {
  symbol: string; // "DIXON.NS"
  base: string; // "DIXON"
  name: string;
  sector: string;
  cap: "large" | "mid" | "small";
  as_of: string | null;
  close: number | null;
  return_1d_pct: number | null;
  volume_ratio: number | null;
  volume: number;
  dist_from_52w_high_pct: number;
  dist_from_52w_low_pct: number;
  stale: boolean;
  error: string | null;
}
export interface PortfolioPayload {
  generated_at: string;
  as_of: string | null;
  count: number;
  ok: number;
  items: PortfolioItem[];
}

// ───────────── Push ─────────────
export interface AlertThresholds { price_move_pct: number; volume_ratio: number; near_52w_pct: number }
export type AlertTrigger = "price_up" | "price_down" | "volume_spike" | "near_52w_high" | "near_52w_low";
export interface EODAlert {
  symbol: string;
  as_of: string;
  triggers: AlertTrigger[];
  price_move_pct: number;
  volume_ratio: number;
  close: number;
  thresholds: AlertThresholds;
  headline: string;
}

export interface ResultsQuarter { revenue: number | null; ebitda: number | null; pbt: number | null; pat: number | null }
export interface ResultsSnapshot {
  source: "screener_quarters" | "filing_pdf_llm" | "unavailable";
  basis: "consolidated" | "standalone";
  ebitda_label: string;
  margin_takeaway: string;
  period_match: boolean;
  notes: string[];
  current: ResultsQuarter;
  prev_qtr: ResultsQuarter | null;
  year_ago: ResultsQuarter | null;
  period_label: string;
  qoq_pct: Record<string, number | null>;
  yoy_pct: Record<string, number | null>;
}
export interface ManagementChangeBio {
  person: string | null;
  role: string | null;
  action: "appointed" | "resigned" | "retired" | "other";
  effective_date: string | null;
  bullets: string[];
  verified: boolean;
  profile_url: string | null;
  note: string;
}

export interface FilingItem {
  id: string;
  symbol: string;
  company: string;
  filed_at: string;
  subject: string;
  body: string;
  attachment_url: string | null;
  category: Category;
  signal_score: number;
  is_noise: boolean;
  one_liner: string | null;
  enrichment: { results?: ResultsSnapshot; management?: ManagementChangeBio } | null;
  classified_by: "rule" | "llm" | "none";
  seq_id: string | null;
  source: "nse" | "cache" | "fallback_seed";
}
export interface FeedItem {
  kind: "alert" | "filing";
  urgency: number;
  symbol: string;
  headline: string;
  alert: EODAlert | null;
  filing: FilingItem | null;
  backfilled: boolean;
  demo: boolean;
}
export interface NoiseDrawerData {
  count: number;
  by_category: Record<string, number>;
  by_symbol: Record<string, number>;
  samples: { id: string; symbol: string; subject: string; filed_at: string; url: string | null }[];
}
export interface PushFeed {
  generated_at: string;
  window_hours: number;
  thresholds: AlertThresholds;
  items: FeedItem[];
  alerts: EODAlert[];
  collapsed_noise: NoiseDrawerData;
  coverage_notes: string[];
  stats: {
    filings_in_window?: number;
    noise_suppressed?: number;
    llm_calls?: number;
    llm_cost_usd?: number;
    latency_s?: number;
    [k: string]: unknown;
  };
}

// ───────────── Pull ─────────────
export interface BusinessSnapshot { what_it_does: string; segments: string[]; revenue_mix: Record<string, number>; citation: string }
export interface FinancialSnapshot {
  as_of_quarter: string;
  basis: "consolidated" | "standalone";
  is_financial: boolean;
  ebitda_label: string;
  ttm_revenue_cr: number | null;
  ttm_ebitda_cr: number | null;
  ebitda_margin_pct: number | null;
  ttm_pat_cr: number | null;
  roce_pct: number | null;
  roe_pct: number | null;
  debt_to_equity: number | null;
  sales_cagr_3y_pct: number | null;
  notes: string[];
  citation: string;
}
export interface MetricPoint {
  label: string;
  unit: string;
  values: Record<string, number | null>;
  cagr_3y_pct: number | null;
  trend: "up" | "down" | "flat" | "n/a";
  citation: string;
}
export interface FinancialMetrics {
  headline: MetricPoint[];
  balance_sheet: MetricPoint[];
  cash_quality: MetricPoint[];
  flags: string[];
}
export type Verdict = "Beat" | "Met" | "Missed" | "In Progress";
export interface NarrativeAuditRow {
  metric_or_theme: string;
  promised_guidance: string;
  actual_delivered: string;
  verdict: Verdict;
  citation: string;
  period: string;
  grounded: boolean;
}
export interface NarrativeVsNumbers {
  rows: NarrativeAuditRow[];
  summary: string;
  credibility_score: number | null;
  tally: Partial<Record<Verdict, number>>;
  basis: "concall" | "annual_report" | "none";
}
export interface CashQualityCheck {
  verdict: "strong" | "adequate" | "weak" | "insufficient_data";
  cfo_to_pat_3y_cum: number | null;
  periods: string[];
  cfo_3y_cr: number | null;
  pat_3y_cr: number | null;
  fcf_3y_sum_cr: number | null;
  watch_flag: boolean;
  commentary: string;
  wc_observations: string[];
  citation: string;
}
export interface GuidanceItem {
  metric: string;
  statement: string;
  horizon: string;
  quote: string | null;
  citation: string;
  period: string;
  source: "concall" | "annual_report";
}
export interface TrajectoryItem { label: string; detail: string; citation: string }
export interface Trajectory { accelerating: TrajectoryItem[]; compressing: TrajectoryItem[] }
export interface CitedPoint { text: string; citation: string; grounded: boolean; by: "llm" | "rule" }
export interface BullBear { bull: CitedPoint[]; bear: CitedPoint[] }

export interface PullReport {
  symbol: string;
  company: string;
  generated_at: string;
  basis: "consolidated" | "standalone";
  mode: "concall" | "annual_report" | "financials_only";
  concall_pages: number;
  concall_periods: string[];
  sources_used: string[];
  business: BusinessSnapshot | null;
  snapshot: FinancialSnapshot | null;
  metrics: FinancialMetrics | null;
  trajectory: Trajectory | null;
  narrative_vs_numbers: NarrativeVsNumbers | null;
  cash_quality: CashQualityCheck | null;
  guidance: GuidanceItem[];
  bull_bear: BullBear | null;
  data_gaps: string[];
  llm_degraded: boolean;
  llm_cost_usd: number;
  latency_ms: number;
  cache_hit: boolean;
}

// ───────────── Telemetry ─────────────
export interface LlmStats {
  provider: string;
  models: { fast: string; deep: string };
  total_calls: number;
  live_calls: number;
  cache_hits: number;
  cache_hit_rate: number;
  failed_calls: number;
  tokens_in: number;
  tokens_out: number;
  total_cost_usd: number;
  est_cost_saved_by_cache_usd: number;
  avg_live_latency_ms: number;
  by_model: { model: string; calls: number; tokens_in: number | null; tokens_out: number | null; cost_usd: number | null; avg_latency_ms: number | null }[];
  by_task: { task: string; tier: "fast" | "deep"; calls: number; cache_hits: number | null; cost_usd: number | null }[];
}
export interface PullCacheStats {
  reports_cached: number;
  hits: number;
  misses: number;
  hit_rate: number;
  saved_usd: number;
  saved_latency_s: number;
  avg_hit_ms: number;
  avg_generation_cost_usd: number;
  avg_generation_ms: number;
}
export interface Telemetry {
  generated_at: string;
  llm: LlmStats;
  pull_cache: PullCacheStats;
  total_saved_usd: number;
  total_cost_usd: number;
}

// ───────────── Static fallback bundle (data/fallback_bundle.json) ─────────────
export interface Bundle {
  schema: number;
  generated_at: string;
  portfolio: PortfolioPayload | null;
  push: PushFeed | null;
  pulls: Record<string, PullReport>;
  errors: Record<string, string>;
}

// ───────────── UI-level ─────────────
export interface Thresholds { window_hours: number; price_thresh: number; vol_mult: number }
