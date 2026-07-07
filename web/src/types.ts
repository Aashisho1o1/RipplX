export type Posture = "critical_review" | "risk_review" | "monitor" | "positive_support" | "insufficient_data";
export type Severity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
export type Signal = "STRONG_REVIEW_SELL" | "TRIM" | "HOLD" | "ACCUMULATE";
export type MetricState = "computed" | "unavailable" | "not_applicable";

export interface MaterialItem { headline: string; event_type: string }
export interface RedFlag { code: string; label: string; severity: Severity; edgar_url: string; quote: string | null }
export interface FilingItem { accession: string; ticker: string; owned: boolean; form: string; filed: string; severity: Severity; posture: Posture | null; watch_label: string | null; material_items: MaterialItem[]; flags: RedFlag[]; manual_review: boolean }
export interface Channel { label: string; direction: string; magnitude: string | null }
export interface WhatChanged { ticker: string; impact_class: string; via: string; net_read: string; channels: Channel[]; guidance: string; liquidity: string; net: string; risk_factor_changes: string | null }
export interface ThesisImpact { ticker: string; verdict: string; no_thesis: boolean }
export interface MetricRow { metric: string; value: string; formula: string; state: MetricState; state_label: string }
export interface IssuerMetrics { ticker: string; owned: boolean; rows: MetricRow[]; empty: string | null }
export interface ShadowSignal { ticker: string; signal: Signal; posture: Posture; rules_fired: string[]; rationale: string | null; counter_evidence: string | null; what_would_change_this: string[]; rationale_withheld: boolean }
export interface Brief { period: { covered: string; filings_in_window: number; analyzed_filings: number }; portfolio: { owned: string[]; watching: string[] }; answer: string; answer_posture: Posture | null; critical_red_flags: FilingItem[]; what_changed: WhatChanged[]; thesis_impact: ThesisImpact[]; verified_numbers: IssuerMetrics[]; open_questions: string[]; boring_filings: string | null; shadow_signals: ShadowSignal[]; tracked_but_unanalyzed: boolean; disclaimer: string; sample_data: boolean }
export interface Verification { verdict: "PASS" | "PASS_WITH_WARNINGS" | "FAIL"; checks: { check_id: string; verdict: string; severity: string; detail: string | null }[] }
export interface PipelineStage { stage: string; label: string; status: string; attempts: number; error: string | null; diagnostics: Record<string, unknown> }
export interface FilingDetail { filing: FilingItem; what_changed: WhatChanged[]; thesis_impact: ThesisImpact[]; verified_numbers: IssuerMetrics | null; verification: Verification | null; shadow_signal: ShadowSignal | null; insufficient_reason: string | null; pipeline: PipelineStage[]; disclaimer: string }
export interface Holding { ticker: string; cik: string; owned: boolean; shares: number | null; cost_basis: number | null; target_weight_pct: number | null; horizon: string | null; thesis: string | null; posture: Posture | null; severity: Severity | null; last_filing: string | null; compressed_verified_read: string | null }
export interface Holdings { owned: Holding[]; watching: Holding[] }
export interface Metrics { ticker: string; owned: boolean; as_of: string; rows: MetricRow[]; empty: string | null; before_first_filing: boolean }
export interface TrackRecord { evaluations: number; posture_counts: Record<Posture, number>; signal_counts: Record<Signal, number>; outcomes_reviewed: number }
export interface Bootstrap { setup_required: boolean; sec_user_agent: string; period: string; signals: boolean; model_extract: string; model_reason: string; api_key_configured: boolean; api_key_source: string | null; analysis_configured: boolean }
export interface Job { id: string; kind: "sync" | "analysis"; state: "queued" | "running" | "completed" | "partial" | "failed"; created_at: string; items: { key: string; state: string; message: string; verdict: string | null; stage: string | null; diagnostics: Record<string, unknown> }[]; error: string | null }
