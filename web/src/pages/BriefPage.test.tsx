import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BootstrapContext } from "../context/BootstrapContext";
import type { Bootstrap, Brief, FilingDigestEntry } from "../types";
import { BriefPage } from "./BriefPage";

const bootstrap: Bootstrap = {
  setup_required: false,
  sec_user_agent: "RipplX Test test@example.com",
  account_email: null,
  period: "90d",
  model: "openai/test",
  provider: "openai",
  api_key_configured: true,
  analysis_configured: true,
};

const reviewed: FilingDigestEntry = {
  accession: "a-1",
  ticker: "AAPL",
  form: "8-K",
  filed: "2026-04-30",
  edgar_url: "https://www.sec.gov/Archives/a-1.htm",
  findings: [],
  withheld: false,
  withheld_reason: null,
  withheld_kind: null,
  outcome: "no_findings",
  dropped_finding_count: 0,
};

function brief(overrides: Partial<Brief> = {}): Brief {
  return {
    period: {
      covered_label: "21 Apr 2026 → today",
      filings_in_window: 0,
      analyzed_filings: 0,
      published_filings: 0,
      withheld_filings: 0,
      filings_tracked_total: 1,
      outside_window: "AAPL 8-K filed 30 Apr 2026 sits outside this reading window.",
    },
    tracked_tickers: ["AAPL"],
    answer: "No tracked filing falls inside your reading window.",
    filings: [],
    gate_removed_filings: [],
    verified_numbers: [],
    open_questions: [],
    reviewed_filings: [reviewed],
    withheld_filings: [],
    tracked_but_unanalyzed: false,
    filings_synced: 1,
    disclaimer: "Educational use only.",
    sample_data: false,
    ...overrides,
  };
}

function renderBrief(payload: Brief, route = "/brief") {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })));
  return render(
    <BootstrapContext.Provider value={{ bootstrap, refresh: vi.fn() }}>
      <MemoryRouter initialEntries={[route]}><BriefPage /></MemoryRouter>
    </BootstrapContext.Provider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("brief truth states", () => {
  it("shows the reading window, names out-of-window filings, and links reviewed filings", async () => {
    renderBrief(brief());
    expect(await screen.findByText("Reading window")).toBeInTheDocument();
    expect(screen.getByText("21 Apr 2026 → today")).toBeInTheDocument();
    expect(screen.getByText("0 of 1")).toBeInTheDocument();
    expect(screen.getByText(/sits outside this reading window/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open settings" })).toHaveAttribute("href", "/settings");
    expect(screen.getByText("Reviewed — nothing material")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /AAPL/ })).toHaveAttribute("href", "/filings/a-1");
    expect(screen.queryByText("Filings are ready for analysis.")).not.toBeInTheDocument();
  });

  it("renders a three-step onboarding checklist driven by real counts", async () => {
    renderBrief(brief({
      period: { ...brief().period, outside_window: null },
      reviewed_filings: [],
      tracked_but_unanalyzed: true,
      filings_synced: 0,
    }));
    expect(await screen.findByText(/No filings downloaded yet, so there is nothing to analyze/)).toBeInTheDocument();
    expect(document.querySelector(".onboarding-actions")).toHaveTextContent("Sync filings from SEC");
    expect(screen.queryByText("Analyze the newest filing")).not.toBeInTheDocument();
    expect(screen.queryByText("Filings are ready for analysis.")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Sync filings from SEC" })).toHaveLength(1);
  });

  it("derives sample chrome from the payload rather than the URL", async () => {
    renderBrief(brief({ reviewed_filings: [], filings: [{ ...reviewed, outcome: "published", findings: [{
      finding_id: "f1", headline: "Risk language changed", severity: "MEDIUM", evidence: [{
        claim_id: "c1", accession: "a-1", section_key: "risk_factors", char_start: 0,
        char_end: 4, quote: "Risk", section_sha256: "a".repeat(64), edgar_url: reviewed.edgar_url,
      }],
    }] }] }), "/brief?demo=1");
    expect(await screen.findByText("Risk language changed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Exit sample" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Sync filings from SEC/ })).toBeInTheDocument();
    cleanup();

    renderBrief(brief({ sample_data: true }), "/brief?demo=1");
    expect(await screen.findByRole("button", { name: "Exit sample" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Sync filings from SEC/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Analyze newest filing/ })).not.toBeInTheDocument();
  });

  it("states publication limits without claiming an analysis happened", async () => {
    renderBrief(brief({
      period: { ...brief().period, outside_window: null },
      reviewed_filings: [],
      tracked_but_unanalyzed: true,
      filings_synced: 1,
    }));
    expect(await screen.findByText(/at most three findings per filing/)).toBeInTheDocument();
    expect(screen.getByText(/same six metrics/)).toBeInTheDocument();
    expect(screen.getByText("No filing has been reviewed yet, so there is nothing to follow up on.")).toBeInTheDocument();
    expect(screen.queryByText("Nothing in this brief needs a follow-up review.")).not.toBeInTheDocument();
  });
});
