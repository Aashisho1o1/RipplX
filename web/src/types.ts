export type Severity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
export type MetricState = "computed" | "unavailable" | "not_applicable" | "withheld";
export type FilingType = "latest" | "10-K" | "10-Q" | "8-K";
export type WithheldKind = "gate" | "pipeline_failed";
export type FilingOutcome = "published" | "no_findings" | "findings_dropped" | "withheld_gate" | "pipeline_failed" | "not_analyzed";

export interface Evidence { claim_id: string; accession: string; section_key: string; char_start: number; char_end: number; quote: string; section_sha256: string; edgar_url: string }
export interface Finding { finding_id: string; headline: string; severity: Severity; metric_id?: string | null; direction?: "up" | "down" | "flat" | null; evidence: Evidence[] }
export interface FilingDigestEntry { accession: string; ticker: string; form: string; filed: string; edgar_url: string; findings: Finding[]; withheld: boolean; withheld_reason: string | null; withheld_kind: WithheldKind | null; outcome: FilingOutcome; dropped_finding_count: number }
export interface MetricInput { concept: string; taxonomy: string; value: string; unit: string; period: string; accession: string }
export interface MetricDerivation { expression: string; formula_version: string; inputs: MetricInput[] }
export interface MetricRow { metric: string; value: string; formula: string; state: MetricState; state_label: string; source_computation_id: number; effective_as_of: string; derivation?: MetricDerivation | null }
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
export interface Bootstrap { setup_required: boolean; sec_user_agent: string; account_email: string | null; period: string; model: string; provider: string | null; api_key_configured: boolean; analysis_configured: boolean; billing_configured: boolean; billing_status: string; showcase_source?: "sec_cache" | "bundled_fixture" | null; showcase_updated_at?: string | null }
export interface AuthChallenge { challenge_id: string; expires_in: number }
export interface Job { id: string; kind: "sync" | "analysis" | "research"; state: "queued" | "running" | "completed" | "partial" | "failed"; created_at: string; items: { key: string; state: string; message: string; verdict: string | null; stage: string | null; reason: string | null; diagnostics: Record<string, unknown> }[]; error: string | null }

export type RiskStatus = "stable" | "watch" | "elevated" | "unavailable";
export interface ProductEvidence { kind: "metric" | "filing" | "thesis" | "promise"; reference_id: string; accession: string | null; section_key: string | null; char_start: number | null; char_end: number | null; quote: string | null; section_sha256: string | null }
export interface RiskLens { lens: string; status: RiskStatus; reason_codes: string[]; explanation: string; metric_ids: string[]; evidence: ProductEvidence[]; comparison_period: string | null; freshness: string | null }
export type ThesisStatus = "draft" | "confirmed" | "supported" | "weakened" | "broken" | "unclear" | "retired";
export interface ThesisItem { item_id: string; kind: "reason" | "risk" | "assumption" | "kill_criterion" | "next_evidence"; text: string; status: ThesisStatus; lens: string | null }
export interface Thesis { items: ThesisItem[] }
export interface ProductProfile { ticker: string; cik: string; monitoring_enabled: boolean; notification_level: "urgent" | "this_week" | "weekly" | "off"; thesis: Thesis; peer_ciks: string[]; updated_at: string }
export interface AttentionEvent { event_id: number | null; event_key: string; ticker: string; cik: string; accession: string | null; priority: "urgent" | "this_week" | "routine"; reason_codes: string[]; risk_changes: string[]; thesis_impacts: string[]; created_at: string; read_at: string | null }
export interface ManagementPromise { promise_id: string; ticker: string; accession: string; section_key: string; char_start: number; char_end: number; section_sha256: string; quote: string; target_period: string | null; target_metric: string | null; status: "open" | "met" | "missed" | "unclear" | "retired" }
export interface PeerComparison { ticker: string; name: string | null; sic_code: string | null; reason: string; caveat: string; risk_statuses: Record<string, RiskStatus>; metrics: Record<string, string> }
export interface ChangeImpact { finding_id: string; headline: string; driver: "revenue" | "earnings" | "cash_flow" | "balance_sheet" | "per_share" | "operations"; effect: "upside" | "downside" | "mixed" | "uncertain"; implication: string; evidence: ProductEvidence[] }
export interface StockImpact { directional_pressure: "upside" | "downside" | "mixed" | "uncertain"; summary: string; watch_next: string; reason_codes: string[]; changes: ChangeImpact[]; formula_version: string }
export type ResearchObligationId = "BUSINESS_ECONOMICS" | "IMPORTANT_CHANGES" | "FINANCIAL_QUALITY_AND_DOWNSIDE" | "PEER_CONTEXT" | "SOURCE_COVERAGE";
export interface ResearchObservation { observation_id: string; tool: string; evidence_label: "fact" | "calculation" | "unavailable"; text: string; evidence: ProductEvidence[]; metric_ids: string[]; as_of: string | null; stable_hash: string }
export interface ResearchInsight { insight_id: string; category: "business" | "change" | "financial_quality" | "peer"; headline: string; evidence_summary: string; driver: string; mechanism: string; implication: string; scenario: "downside" | "upside" | "mixed" | "neutral"; assumptions: string[]; limitations: string[]; observation_ids: string[]; evidence_status: "conditional_inference" }
export interface CompanyResearchReport { schema_version: "company_research.v2"; ticker: string; cik: string; as_of: string; data_cutoff: string; summary: string; obligations: { obligation: ResearchObligationId; state: "supported" | "mixed" | "unavailable" }[]; insights: ResearchInsight[]; observations: ResearchObservation[]; evidence_gaps: string[]; disclaimer: string }
export interface CompanyResearchTrace { schema_version: "company_research_trace.v2"; tool_calls: { tool: string; arguments_sha256: string; result_sha256: string; cached: boolean }[]; obligation_transitions: { obligation: ResearchObligationId; state: "supported" | "mixed" | "unavailable" }[]; tool_budget_used: number; turn_budget_used: number; repair_used: boolean; dropped_insights: Record<string, string[]>; model: string; prompt_version: string; compiler_version: string; terminal_reason: string }
export interface ResearchRun { run_id: string; ticker: string; cik: string; status: "queued" | "running" | "completed" | "partial" | "failed"; input_hash: string; report: CompanyResearchReport | null; trace: CompanyResearchTrace | null; created_at: string; completed_at: string | null }
export interface CompanyResearch { ticker: string; cik: string; company_name: string | null; as_of: string; attention: AttentionEvent[]; risks: RiskLens[]; business_summary: string | null; business_evidence: ProductEvidence | null; recent_filings: FilingDigestEntry[]; metrics: Metrics; impact: StockImpact; profile: ProductProfile; thesis: Thesis; promises: ManagementPromise[]; peers: PeerComparison[]; manual_peer_tickers: string[]; certificate_urls: string[]; deep_research: ResearchRun | null; disclaimer: string }
export interface Alerts { events: AttentionEvent[] }
