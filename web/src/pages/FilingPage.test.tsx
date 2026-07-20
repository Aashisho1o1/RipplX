import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FilingDetail, ResearchTrace } from "../types";
import { FilingPage, researchOutcomeLabel } from "./FilingPage";

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
    section_sha256: "abc",
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
    },
    verified_numbers: null,
    verification: { verdict: "PASS", checks: [] },
    withheld_reason: null,
    pipeline: [],
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

describe("filing research audit", () => {
  it("maps every publication outcome to explicit user language", () => {
    expect(researchOutcomeLabel("published")).toBe("Published");
    expect(researchOutcomeLabel("partial")).toBe(
      "Published with some findings dropped",
    );
    expect(researchOutcomeLabel("metrics_only")).toBe(
      "Metrics only — no qualitative findings published",
    );
    expect(researchOutcomeLabel("withheld")).toBe(
      "Withheld — no qualitative findings published",
    );
  });

  it("shows a partial result, its dropped code, and its certificate", async () => {
    const value = detail("partial");
    value.research!.dropped_findings = [{
      finding_id: "f2",
      error_codes: ["QUOTE_NOT_EXACT"],
    }];
    renderDetail(value);

    const outcome = await screen.findByText("Outcome:");
    expect(outcome.parentElement).toHaveTextContent("Published with some findings dropped");
    expect(screen.getByText(/f2: QUOTE_NOT_EXACT/)).toBeInTheDocument();
    expect(screen.getByText(finding.headline)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download verification certificate" }))
      .toHaveAttribute("href", "/api/filings/a-1/certificate?download=true");
  });

  it("never renders withheld findings and only offers a certificate when provided", async () => {
    renderDetail(detail("withheld", {
      filing: {
        ...detail("withheld").filing,
        withheld: true,
        findings: [{ ...finding, headline: "Unverified finding must stay hidden" }],
      },
      withheld_reason: "LLM-derived analysis withheld.",
      certificate_url: null,
    }));

    const outcome = await screen.findByText("Outcome:");
    expect(outcome.parentElement).toHaveTextContent(
      "Withheld — no qualitative findings published",
    );
    expect(screen.queryByText("Unverified finding must stay hidden")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Download verification certificate" }))
      .not.toBeInTheDocument();
  });
});
