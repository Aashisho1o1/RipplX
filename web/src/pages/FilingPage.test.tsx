import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FilingDetail, ResearchTrace } from "../types";
import { FilingPage, outcomeHeadline, researchOutcomeLabel, terminalReasonLabel } from "./FilingPage";

const finding = {
  finding_id: "f1",
  headline: "Liquidity disclosure changed",
  severity: "HIGH" as const,
  evidence: [{
    claim_id: "c1",
    accession: "a-1",
    section_key: "mdna",
    char_start: 0,
    char_end: 17,
    quote: "Liquidity changed",
    section_sha256: "abc123456789def",
    edgar_url: "https://www.sec.gov/Archives/a-1.htm",
  }],
};

function detail(
  outcome: ResearchTrace["outcome"],
  overrides: Partial<FilingDetail> = {},
): FilingDetail {
  return {
    filing: {
      accession: "a-1",
      ticker: "TEST",
      form: "10-Q",
      filed: "2026-07-01",
      edgar_url: "https://www.sec.gov/Archives/a-1.htm",
      findings: [finding],
      withheld: false,
      withheld_reason: null,
      withheld_kind: null,
      outcome: "published",
      dropped_finding_count: 0,
    },
    verified_numbers: null,
    verification: { verdict: "PASS", checks: [] },
    withheld_reason: null,
    pipeline: [{ stage: "parse", label: "Parse filing", status: "completed", attempts: 2, error: null, diagnostics: { sections_found: ["mdna"] } }],
    research: {
      outcome,
      terminal_reason: "verified",
      tool_call_count: 2,
      tool_names: ["search_sections", "get_metric"],
      repair_used: false,
      dropped_findings: [],
    },
    certificate_url: "/api/filings/a-1/certificate",
    disclaimer: "Educational use only.",
    sample_data: false,
    ...overrides,
  };
}

function renderDetail(value: FilingDetail) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })));
  render(
    <MemoryRouter initialEntries={["/filings/a-1"]}>
      <Routes><Route path="/filings/:accession" element={<FilingPage />} /></Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("filing trust surface", () => {
  it("maps all publication outcomes and terminal reasons to plain language", () => {
    expect(researchOutcomeLabel("published")).toMatch(/Published with deterministic/);
    expect(researchOutcomeLabel("partial")).toMatch(/unsupported findings removed/);
    expect(researchOutcomeLabel("metrics_only")).toMatch(/Metrics published/);
    expect(researchOutcomeLabel("withheld")).toMatch(/held back/);
    expect(terminalReasonLabel("provider_failed")).toBe("The model provider was unavailable");
    expect(terminalReasonLabel("new_safe_reason")).toBe("New safe reason");
  });

  it.each([
    ["published", "Published with deterministic evidence checks"],
    ["partial", "Published with unsupported findings removed"],
    ["metrics_only", "Metrics published; no qualitative finding passed the gate"],
    ["withheld", "Analysis held back; no qualitative content was published"],
  ] as const)("renders the %s outcome", async (outcome, label) => {
    renderDetail(detail(outcome));
    expect(await screen.findByText(label)).toBeInTheDocument();
  });

  it("shows terminal reason, retry attempts, drop codes, and human explanations", async () => {
    const value = detail("partial");
    value.research!.terminal_reason = "skeptic_blocked";
    value.research!.dropped_findings = [{ finding_id: "f2", error_codes: ["QUOTE_NOT_EXACT", "FUTURE_CODE"] }];
    renderDetail(value);

    expect(await screen.findAllByText("A reviewer objection was left unresolved")).not.toHaveLength(0);
    expect(screen.getByText("attempt 2 of 2")).toBeInTheDocument();
    expect(screen.getByText("QUOTE_NOT_EXACT")).toHaveAttribute("title", "The quotation did not match the filing exactly.");
    expect(screen.getByText("FUTURE_CODE")).not.toHaveAttribute("title");
    expect(screen.getByText(finding.headline)).toBeInTheDocument();
  });

  it("renders partial publication as a verified per-finding outcome", async () => {
    const value = detail("partial");
    value.research!.dropped_findings = [{ finding_id: "f2", error_codes: ["DUPLICATE_EVIDENCE"] }];
    renderDetail(value);
    expect(await screen.findByText("1 finding published, 1 finding removed by the evidence gate")).toBeInTheDocument();
    expect(screen.getByLabelText("Publication outcome").querySelector(".outcome-glyph")).toHaveTextContent("✓");
    expect(outcomeHeadline("partial", 2, 1)).toBe("2 findings published, 1 finding removed by the evidence gate");
  });

  it("never renders withheld findings or an affirmative evidence badge", async () => {
    renderDetail(detail("withheld", {
      filing: {
        ...detail("withheld").filing,
        withheld: true,
        withheld_kind: "gate",
        outcome: "withheld_gate",
        findings: [{ ...finding, headline: "Unverified finding must stay hidden" }],
      },
      withheld_reason: "LLM-derived analysis withheld.",
    }));

    expect(await screen.findByText(/Analysis held back; no qualitative content/)).toBeInTheDocument();
    expect(screen.queryByText("Unverified finding must stay hidden")).not.toBeInTheDocument();
    expect(screen.queryByText("Exact evidence checked")).not.toBeInTheDocument();
  });
});
