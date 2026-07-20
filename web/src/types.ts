export type Severity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
export type MetricState = "computed" | "unavailable" | "not_applicable" | "withheld";
export type FilingType = "latest" | "10-K" | "10-Q" | "8-K";
export type WithheldKind = "gate" | "pipeline_failed";
export type FilingOutcome = "published" | "no_findings" | "findings_dropped" | "withheld_gate" | "pipeline_failed" | "not_analyzed";

export interface Evidence { claim_id: string; accession: string; section_key: string; char_start: number; char_end: number; quote: string; section_sha256: string; edgar_url: string }
export interface Finding { finding_id: string; headline: string; severity: Severity; evidence: Evidence[] }
export interface FilingDigestEntry { accession: string; ticker: string; form: string; filed: string; edgar_url: string; findings: Finding[]; withheld: boolean; withheld_reason: string | null; withheld_kind: WithheldKind | null; outcome: FilingOutcome; dropped_finding_count: number }
export interface MetricRow { metric: string; value: string; formula: string; state: MetricState; state_label: string; source_computation_id: number; effective_as_of: string }
export interface IssuerMetrics { ticker: string; rows: MetricRow[]; empty: string | null; summary: string }
export interface Brief { period: { covered_label: string; filings_in_window: number; analyzed_filings: number; published_filings: number; withheld_filings: number; filings_tracked_total: number; outside_window: string | null }; tracked_tickers: string[]; answer: string; filings: FilingDigestEntry[]; gate_removed_filings: FilingDigestEntry[]; verified_numbers: IssuerMetrics[]; open_questions: string[]; reviewed_filings: FilingDigestEntry[]; withheld_filings: FilingDigestEntry[]; tracked_but_unanalyzed: boolean; filings_synced: number; disclaimer: string; sample_data: boolean }
export interface VerificationCheck { check_id: string; verdict: string; severity: string; detail: string | null }
export interface Verification { verdict: "PASS" | "PASS_WITH_WARNINGS" | "FAIL"; checks: VerificationCheck[] }
export interface PipelineStage { stage: string; label: string; status: string; attempts: number; error: string | null; diagnostics: Record<string, unknown> }
export interface DroppedFinding { finding_id: string; error_codes: string[] }
export interface ResearchTrace { outcome: "published" | "partial" | "metrics_only" | "withheld"; terminal_reason: string; tool_call_count: number; tool_names: string[]; repair_used: boolean; dropped_findings: DroppedFinding[] }
export interface Certificate {
  schema_version: string;
  certificate_sha256: string;
  p1_analysis_id: number;
  trace_analysis_id: number;
  p1_output_sha256: string;
  filing: Record<string, unknown>;
  outcome: string;
  terminal_reason: string;
  published_finding_ids: string[];
  dropped_findings: DroppedFinding[];
  classification: string | null;
  evidence: Record<string, unknown>[];
  metrics: Record<string, unknown>[];
  verification: VerificationCheck[];
  tool_calls: Record<string, unknown>[];
  agenda: Record<string, unknown>[];
  models: Record<string, unknown>;
  prompts: Record<string, unknown>;
  budgets: Record<string, unknown>;
}
export interface FilingDetail { filing: FilingDigestEntry; verified_numbers: IssuerMetrics | null; verification: Verification | null; withheld_reason: string | null; pipeline: PipelineStage[]; research: ResearchTrace | null; certificate_url: string | null; disclaimer: string; sample_data: boolean }
export interface TrackedCompany { ticker: string; cik: string; newest_supported_filing: string | null; compressed_verified_read: string | null }
export interface Companies { companies: TrackedCompany[] }
export interface Metrics { ticker: string; as_of: string; rows: MetricRow[]; empty: string | null; summary: string; before_first_filing: boolean }
export interface Bootstrap { setup_required: boolean; sec_user_agent: string; account_email: string | null; period: string; model: string; provider: string | null; api_key_configured: boolean; analysis_configured: boolean }
export interface AuthChallenge { challenge_id: string; expires_in: number }
export interface Job { id: string; kind: "sync" | "analysis"; state: "queued" | "running" | "completed" | "partial" | "failed"; created_at: string; items: { key: string; state: string; message: string; verdict: string | null; stage: string | null; reason: string | null; diagnostics: Record<string, unknown> }[]; error: string | null }
