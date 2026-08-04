import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BootstrapContext } from "../context/BootstrapContext";
import type { Bootstrap, CompanyResearch, Job } from "../types";
import { CompanyPage } from "./CompanyPage";

const bootstrap: Bootstrap = {
  setup_required: false,
  sec_user_agent: "RipplX Test test@example.com",
  account_email: null,
  period: "90d",
  model: "test/model",
  provider: "test",
  api_key_configured: true,
  analysis_configured: true,
  billing_configured: false,
  billing_status: "free",
};

const evidence = {
  kind: "filing" as const,
  reference_id: "a-1:risk_factors:0:31",
  accession: "a-1",
  section_key: "risk_factors",
  char_start: 0,
  char_end: 31,
  quote: "Revenue declined in the period.",
  section_sha256: "a".repeat(64),
};

const research: CompanyResearch = {
  ticker: "ACME",
  cik: "0000000001",
  company_name: "Acme Corp",
  as_of: "2026-08-01",
  attention: [],
  risks: [{ lens: "operating_performance", status: "elevated", reason_codes: ["REVENUE_DOWN"], explanation: "Revenue and cash flow weakened.", metric_ids: ["revenue_growth"], evidence: [evidence], comparison_period: "annual", freshness: "2026-08-01" }],
  business_summary: "Acme sells verified widgets.",
  business_evidence: evidence,
  recent_filings: [{ accession: "a-1", ticker: "ACME", form: "10-K", filed: "2026-08-01", edgar_url: "https://www.sec.gov/Archives/a-1.htm", findings: [], withheld: false, withheld_reason: null, withheld_kind: null, outcome: "published", dropped_finding_count: 0 }],
  metrics: { ticker: "ACME", as_of: "2026-08-01", rows: [{ metric: "Revenue growth", value: "-8.0%", formula: "revenue_growth.v5", state: "computed", state_label: "Computed", source_computation_id: 1, effective_as_of: "2026-08-01" }], empty: null, summary: "One verified operating measure declined.", before_first_filing: false },
  valuation: {
    run_id: "v-1", ticker: "ACME", price: 10, price_as_of: "2026-08-01", status: "computed", label: "Demanding", explanation: "Price requires stronger cash-flow growth.", assumptions: { discount_rate: 0.1, terminal_growth: 0.025, conservative_growth: 0, base_growth: 0.05, optimistic_growth: 0.1 }, scenarios: [
      { name: "conservative", growth: 0, implied_value_per_share: 7, change_percent: -30 },
      { name: "base", growth: 0.05, implied_value_per_share: 9, change_percent: -10 },
      { name: "optimistic", growth: 0.1, implied_value_per_share: 12, change_percent: 20 },
    ], reverse_dcf_growth: 0.08, trailing_pe: 18.2, price_to_fcf: 21.4, fcf_yield: 0.0467, inputs: [evidence], formula_version: "reverse_dcf.v2", certificate_hash: "b".repeat(64), created_at: "2026-08-01T00:00:00Z",
  },
  impact: { directional_pressure: "downside", summary: "Verified evidence currently points to more downside than upside pressure.", priced_in: "The saved price is demanding under the selected assumptions.", watch_next: "Watch revenue and cash flow in the next filing.", reason_codes: ["VERIFIED_REVENUE_DOWN"], changes: [{ finding_id: "f1", headline: "Revenue declined from the prior period.", driver: "revenue", effect: "downside", implication: "This may weaken future cash flows if it persists.", evidence: [evidence] }], formula_version: "stock_impact.v1" },
  profile: { ticker: "ACME", cik: "0000000001", monitoring_enabled: true, notification_level: "weekly", thesis: { items: [] }, peer_ciks: [], updated_at: "2026-08-01T00:00:00Z" },
  thesis: { items: [{ item_id: "w1", kind: "next_evidence", text: "Revenue returns to growth.", status: "confirmed", lens: "operating_performance" }] },
  promises: [],
  peers: [],
  manual_peer_tickers: [],
  questions: ["What is the biggest verified downside right now?"],
  certificate_urls: ["/api/filings/a-1/certificate"],
  deep_research: null,
  disclaimer: "Educational decision support only.",
};

function renderCompany(fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(research), { status: 200, headers: { "Content-Type": "application/json" } }))) {
  vi.stubGlobal("fetch", fetcher);
  return render(<BootstrapContext.Provider value={{ bootstrap, refresh: vi.fn() }}><MemoryRouter initialEntries={["/companies/ACME?demo=1"]}><Routes><Route path="/companies/:ticker" element={<CompanyPage />} /></Routes></MemoryRouter></BootstrapContext.Provider>);
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("company decision brief", () => {
  it("combines impact, financial health, valuation, changes, and watch conditions", async () => {
    const view = renderCompany();
    expect(await screen.findByText(/What deserves attention/)).toBeInTheDocument();
    expect(screen.getByText(/Connected research/)).toBeInTheDocument();
    expect(screen.getByText(/Financial X-Ray/)).toBeInTheDocument();
    expect(screen.getByRole("table", { name: /Deterministic SEC XBRL metric results/ })).toBeVisible();
    expect(view.container.querySelector("details.xray-metrics")).toBeNull();
    expect(screen.queryByText("No opaque score")).not.toBeInTheDocument();
    expect(await screen.findByText("18.2×")).toBeInTheDocument();
    expect(screen.getByText("21.4×")).toBeInTheDocument();
    expect(screen.getByText("4.7%")).toBeInTheDocument();
    expect(screen.getByText("-30.0% vs price")).toBeInTheDocument();
    expect(screen.getByText("Change → driver → conditional implication")).toBeInTheDocument();
    expect(screen.getAllByText("This may weaken future cash flows if it persists.")).toHaveLength(2);
    expect(screen.getByText("Saved watch conditions")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Revenue returns to growth.")).toBeInTheDocument();
  });

  it("keeps the verified change connection useful when optional deep research fails", async () => {
    const failed = {
      ...research,
      deep_research: {
        run_id: "4".repeat(32), ticker: "ACME", cik: research.cik, status: "failed" as const,
        input_hash: "5".repeat(64), report: null, trace: null,
        created_at: "2026-08-03T00:00:00Z", completed_at: "2026-08-03T00:00:05Z",
      },
    };
    renderCompany(vi.fn().mockResolvedValue(new Response(JSON.stringify(failed), { status: 200, headers: { "Content-Type": "application/json" } })));

    expect(await screen.findByText("Verified filing connection")).toBeInTheDocument();
    expect(screen.getAllByText("Revenue declined from the prior period.")).toHaveLength(2);
    expect(screen.getAllByText("This may weaken future cash flows if it persists.")).toHaveLength(2);
    expect(screen.getByText(/Optional deeper synthesis did not finish/)).toBeInTheDocument();
    expect(screen.queryByText("Deep research did not complete")).not.toBeInTheDocument();
  });

  it("uses collected evidence and prioritizes risks when every drafted insight is withheld", async () => {
    const emptyDeep = {
      ...research,
      impact: { ...research.impact, changes: [] },
      risks: [
        { ...research.risks[0], status: "watch" as const, explanation: "Revenue and cash flow need review." },
        { ...research.risks[0], lens: "leverage", status: "stable" as const, reason_codes: ["LEVERAGE_MANAGEABLE"], explanation: "Leverage is within the stable range." },
        { ...research.risks[0], lens: "concentration", status: "unavailable" as const, reason_codes: ["CONCENTRATION_NOT_STRUCTURED"], explanation: "Concentration is not yet structured." },
      ],
      deep_research: {
        run_id: "6".repeat(32), ticker: "ACME", cik: research.cik, status: "partial" as const,
        input_hash: "7".repeat(64),
        report: {
          schema_version: "company_research.v1" as const, ticker: "ACME", cik: research.cik,
          as_of: "2026-08-01", data_cutoff: "2026-08-03",
          summary: "No qualitative insight passed the deterministic research compiler.",
          obligations: [], insights: [],
          observations: [{
            observation_id: "o_" + "8".repeat(16), tool: "get_financial_context",
            evidence_label: "calculation" as const, text: "Revenue Growth: -8.0%.",
            evidence: [{ ...evidence, kind: "metric" as const, reference_id: "computation:1:revenue_growth", accession: null, section_key: null, char_start: null, char_end: null, quote: null, section_sha256: null }],
            metric_ids: ["revenue_growth"], as_of: "2026-08-01", stable_hash: "8".repeat(64),
          }],
          valuation_context: null, evidence_gaps: [], disclaimer: research.disclaimer,
        },
        trace: {
          schema_version: "company_research_trace.v1" as const, tool_calls: [], obligation_transitions: [],
          tool_budget_used: 1, turn_budget_used: 3, repair_used: true,
          dropped_insights: { i1: ["UNSAFE_AUTHORED_TEXT"] }, model: "test/model",
          prompt_version: "Company_research.v1", compiler_version: "company_research_compiler.v1",
          terminal_reason: "submitted",
        },
        created_at: "2026-08-03T00:00:00Z", completed_at: "2026-08-03T00:00:05Z",
      },
    };
    renderCompany(vi.fn().mockResolvedValue(new Response(JSON.stringify(emptyDeep), { status: 200, headers: { "Content-Type": "application/json" } })));

    expect(await screen.findAllByText("Revenue and cash flow need review.")).toHaveLength(2);
    expect(screen.getByText("Verified evidence collected")).toBeInTheDocument();
    expect(screen.getByText("Revenue Growth: -8.0%.")).toBeVisible();
    expect(screen.getByText("1 needs review")).toBeInTheDocument();
    expect(screen.getByText(/Stable and unavailable checks/)).toBeInTheDocument();
    expect(screen.queryByText("The evidence is connected")).not.toBeInTheDocument();
    expect(screen.queryByText(/No qualitative insight passed/)).not.toBeInTheDocument();
    expect(screen.queryByText("REVENUE_DOWN")).not.toBeInTheDocument();
  });

  it("renders compiler-passing connected research without a stock-direction vote", async () => {
    const deep = {
      ...research,
      deep_research: {
        run_id: "c".repeat(32),
        ticker: "ACME",
        cik: "0000000001",
        status: "partial" as const,
        input_hash: "d".repeat(64),
        report: {
          schema_version: "company_research.v1" as const,
          ticker: "ACME",
          cik: "0000000001",
          as_of: "2026-08-01",
          data_cutoff: "2026-08-03",
          summary: "Verified evidence connects operating pressure with cash conversion.",
          obligations: [
            { obligation: "BUSINESS_ECONOMICS" as const, state: "mixed" as const },
            { obligation: "IMPORTANT_CHANGES" as const, state: "supported" as const },
            { obligation: "FINANCIAL_QUALITY_AND_DOWNSIDE" as const, state: "supported" as const },
            { obligation: "VALUATION_CONTEXT" as const, state: "unavailable" as const },
            { obligation: "PEER_CONTEXT" as const, state: "unavailable" as const },
            { obligation: "SOURCE_COVERAGE" as const, state: "supported" as const },
          ],
          insights: [{
            insight_id: "i1",
            category: "change" as const,
            headline: "A verified operating change affects cash conversion.",
            evidence_summary: "The cited filing passage establishes the change.",
            driver: "Operating pressure",
            mechanism: "cash_conversion",
            implication: "If the condition persists, cash generation may weaken.",
            scenario: "downside" as const,
            assumptions: ["The condition persists."],
            limitations: ["Only filed SEC evidence was used."],
            observation_ids: ["o_" + "e".repeat(16)],
            evidence_status: "conditional_inference" as const,
          }],
          observations: [{
            observation_id: "o_" + "e".repeat(16),
            tool: "get_verified_changes",
            evidence_label: "fact" as const,
            text: "Revenue declined in the period.",
            evidence: [evidence],
            metric_ids: [],
            as_of: "2026-08-01",
            stable_hash: "e".repeat(64),
          }],
          valuation_context: null,
          evidence_gaps: ["Enter a current price and date to add valuation context."],
          disclaimer: research.disclaimer,
        },
        trace: {
          schema_version: "company_research_trace.v1" as const,
          tool_calls: [{ tool: "get_verified_changes", arguments_sha256: "f".repeat(64), result_sha256: "a".repeat(64), cached: false }],
          obligation_transitions: [],
          tool_budget_used: 1,
          turn_budget_used: 2,
          repair_used: false,
          dropped_insights: {},
          model: "test/model",
          prompt_version: "Company_research.v1",
          compiler_version: "company_research_compiler.v1",
          terminal_reason: "submitted",
        },
        created_at: "2026-08-01T00:00:00Z",
        completed_at: "2026-08-01T00:00:05Z",
      },
    };
    renderCompany(vi.fn().mockResolvedValue(new Response(JSON.stringify(deep), { status: 200, headers: { "Content-Type": "application/json" } })));

    expect(await screen.findAllByText("A verified operating change affects cash conversion.")).toHaveLength(2);
    expect(screen.getAllByText("If the condition persists, cash generation may weaken.")).toHaveLength(2);
    expect(screen.queryByText(/downside pressure/i)).not.toBeInTheDocument();
  });

  it("keeps a fail-closed demo valuation visible without refreshing ephemeral demo state", async () => {
    const emptyBrief = { ...research, valuation: null };
    const unavailable = { ...research.valuation!, status: "unavailable" as const, label: "Unavailable" as const, explanation: "Required SEC inputs are unreliable.", scenarios: [], reverse_dcf_growth: null, trailing_pe: null, price_to_fcf: null, fcf_yield: null };
    const fetcher = vi.fn().mockImplementation((_input: RequestInfo | URL, init?: RequestInit) => Promise.resolve(new Response(JSON.stringify(init?.method === "POST" ? unavailable : emptyBrief), { status: 200, headers: { "Content-Type": "application/json" } })));
    renderCompany(fetcher);
    fireEvent.change(await screen.findByLabelText("Price you want to evaluate"), { target: { value: "400" } });

    fireEvent.click(screen.getByRole("button", { name: "Calculate scenarios" }));

    expect(await screen.findByText("Unavailable under these assumptions")).toBeInTheDocument();
    expect(screen.getByText("Verified filing connection")).toBeInTheDocument();
    expect(screen.getByText("Required SEC inputs are unreliable.")).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("continues a research launch from filing analysis into connected research", async () => {
    const analysisJob: Job = {
      id: "1".repeat(32), kind: "analysis", state: "running", created_at: "t",
      items: [], error: null,
    };
    const queuedResearch = {
      run_id: "2".repeat(32), ticker: "ACME", cik: research.cik, status: "queued",
      input_hash: "3".repeat(64), report: null, trace: null,
      created_at: "2026-08-03T00:00:00Z", completed_at: null,
    };
    const fetcher = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/companies/ACME/research" && !init?.method) return Promise.resolve(new Response(JSON.stringify(research), { status: 200, headers: { "Content-Type": "application/json" } }));
      if (url === `/api/jobs/${analysisJob.id}`) return Promise.resolve(new Response(JSON.stringify({ ...analysisJob, state: "completed" }), { status: 200, headers: { "Content-Type": "application/json" } }));
      if (url === "/api/companies/ACME/research-runs" && init?.method === "POST") return Promise.resolve(new Response(JSON.stringify(queuedResearch), { status: 202, headers: { "Content-Type": "application/json" } }));
      if (url === `/api/research-runs/${queuedResearch.run_id}`) return Promise.resolve(new Response(JSON.stringify(queuedResearch), { status: 200, headers: { "Content-Type": "application/json" } }));
      throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
    });
    vi.stubGlobal("fetch", fetcher);
    render(
      <BootstrapContext.Provider value={{ bootstrap, refresh: vi.fn() }}>
        <MemoryRouter initialEntries={[{ pathname: "/companies/ACME", state: { initialJob: analysisJob, autoResearch: true } }]}>
          <Routes><Route path="/companies/:ticker" element={<CompanyPage />} /></Routes>
        </MemoryRouter>
      </BootstrapContext.Provider>,
    );

    expect(await screen.findByText("Connecting evidence…", {}, { timeout: 3000 })).toBeInTheDocument();
    expect(fetcher.mock.calls.some(([url, init]) => String(url) === "/api/companies/ACME/research-runs" && (init as RequestInit | undefined)?.method === "POST")).toBe(true);
  });
});
