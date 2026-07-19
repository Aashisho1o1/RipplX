export type Posture = "critical_review" | "risk_review" | "monitor" | "positive_support" | "insufficient_data";
export type Severity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
export type MetricState = "computed" | "unavailable" | "not_applicable";
export type FilingType = "latest" | "10-K" | "10-Q" | "8-K";

export interface Evidence { claim_id: string; accession: string; section_key: string; char_start: number; char_end: number; quote: string; section_sha256: string; edgar_url: string }
export interface Finding { finding_id: string; headline: string; severity: Severity; evidence: Evidence[] }
export interface FilingDigestEntry { accession: string; ticker: string; form: string; filed: string; edgar_url: string; findings: Finding[]; withheld: boolean; withheld_reason: string | null }
export interface MetricRow { metric: string; value: string; formula: string; state: MetricState; state_label: string; source_computation_id: number; effective_as_of: string }
export interface IssuerMetrics { ticker: string; rows: MetricRow[]; empty: string | null }
export interface Brief { period: { covered: string; filings_in_window: number; analyzed_filings: number }; tracked_tickers: string[]; answer: string; answer_posture: Posture | null; filings: FilingDigestEntry[]; verified_numbers: IssuerMetrics[]; open_questions: string[]; boring_filings: string | null; withheld_filings: FilingDigestEntry[]; tracked_but_unanalyzed: boolean; disclaimer: string; sample_data: boolean }
export interface Verification { verdict: "PASS" | "PASS_WITH_WARNINGS" | "FAIL"; checks: { check_id: string; verdict: string; severity: string; detail: string | null }[] }
export interface PipelineStage { stage: string; label: string; status: string; attempts: number; error: string | null; diagnostics: Record<string, unknown> }
export interface DroppedFinding { finding_id: string; error_codes: string[] }
export interface ResearchTrace { outcome: "published" | "partial" | "metrics_only" | "withheld"; terminal_reason: string; tool_call_count: number; tool_names: string[]; repair_used: boolean; dropped_findings: DroppedFinding[] }
export interface FilingDetail { filing: FilingDigestEntry; verified_numbers: IssuerMetrics | null; verification: Verification | null; withheld_reason: string | null; pipeline: PipelineStage[]; research: ResearchTrace | null; certificate_url: string | null; disclaimer: string }
export interface TrackedCompany { ticker: string; cik: string; last_filing: string | null; compressed_verified_read: string | null }
export interface Companies { companies: TrackedCompany[] }
export interface Metrics { ticker: string; as_of: string; rows: MetricRow[]; empty: string | null; before_first_filing: boolean }
export interface Bootstrap { setup_required: boolean; sec_user_agent: string; account_email: string | null; period: string; model: string; provider: string | null; api_key_configured: boolean; analysis_configured: boolean }
export interface AuthChallenge { challenge_id: string; expires_in: number }
export interface Job { id: string; kind: "sync" | "analysis"; state: "queued" | "running" | "completed" | "partial" | "failed"; created_at: string; items: { key: string; state: string; message: string; verdict: string | null; stage: string | null; diagnostics: Record<string, unknown> }[]; error: string | null }
