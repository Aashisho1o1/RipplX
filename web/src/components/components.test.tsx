import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FilingItemCard } from "./FilingItemCard";
import { FilingTypePicker } from "./FilingTypePicker";
import { FindingList } from "./FindingList";
import { MetricTable } from "./MetricTable";
import { JobProgress } from "./JobProgress";

afterEach(cleanup);

describe("trust vocabulary", () => {
  it("lets the user choose a supported filing family", () => {
    const onChange = vi.fn();
    render(<FilingTypePicker value="latest" onChange={onChange} />);

    fireEvent.click(screen.getByText("Quarterly report"));

    expect(onChange).toHaveBeenCalledWith("10-Q");
    expect(screen.getByRole("radio", { name: /Latest filing/ })).toBeChecked();
  });
  it("marks the selected filing family without relying on colour alone", () => {
    const { container } = render(<FilingTypePicker value="10-K" onChange={vi.fn()} />);
    const annual = screen.getByRole("radio", { name: /Annual report/ });
    expect(annual).toBeChecked();
    expect(annual.closest(".filing-option")).toHaveClass("selected");
    expect(container.querySelectorAll(".filing-option.selected")).toHaveLength(1);
    expect(annual.closest(".filing-option")?.querySelector(".filing-option-check")).toHaveTextContent("✓");
  });
  it("distinguishes all four presentation metric states", () => {
    const { container } = render(<MetricTable summary="four states of 6 starter metrics" rows={[
      { metric: "Revenue growth", value: "+17.5%", formula: "revenue_growth.v1", state: "computed", state_label: "Computed from SEC XBRL facts", source_computation_id: 41, effective_as_of: "2025-06-30" },
      { metric: "CFO trend", value: "— data missing", formula: "cfo_trend.v1", state: "unavailable", state_label: "Data missing", source_computation_id: 42, effective_as_of: "2025-06-30" },
      { metric: "Net debt / (operating income + D&A) proxy", value: "— not applicable", formula: "simple_leverage.v1", state: "not_applicable", state_label: "Not applicable for this issuer", source_computation_id: 43, effective_as_of: "2025-06-30" },
      { metric: "Liquidity", value: "— withheld", formula: "liquidity_basics.v2", state: "withheld", state_label: "Withheld — the stored result failed provenance re-validation", source_computation_id: 44, effective_as_of: "2025-06-30" },
    ]} />);
    expect(screen.getByLabelText("Computed from SEC XBRL facts")).toHaveTextContent("✓");
    expect(screen.getByLabelText("Data missing")).toHaveTextContent("Unavailable");
    expect(screen.getByLabelText("Not applicable for this issuer")).toHaveTextContent("Not applicable");
    expect(screen.getByLabelText(/stored result failed/)).toHaveTextContent("Withheld");
    expect(container.querySelector(".trust.missing")).toBeNull();
    expect(screen.getByText(/computation #41/)).toHaveTextContent("computed as of 2025-06-30");
  });

  it("renders exact evidence as text and links only to SEC HTTPS citations", () => {
    const { container } = render(<FindingList findings={[{
      finding_id: "finding-1",
      headline: "<b>Liquidity disclosure changed</b>",
      severity: "HIGH",
      evidence: [{
        claim_id: "claim-1", accession: "0000000000-25-000001", section_key: "mdna",
        char_start: 120, char_end: 153, quote: "Cash available under the facility declined.",
        section_sha256: "abc123", edgar_url: "https://www.sec.gov/Archives/example.htm",
      }],
    }]} />);

    expect(screen.getByText("<b>Liquidity disclosure changed</b>")).toBeInTheDocument();
    expect(container.querySelector("b")).toBeNull();
    expect(screen.getByText("Cash available under the facility declined.")).toHaveTextContent("Cash available under the facility declined.");
    expect(screen.getByRole("link", { name: /View filing on EDGAR/ })).toHaveAttribute("href", "https://www.sec.gov/Archives/example.htm");
  });

  it("withholds findings whenever a filing requires manual review", () => {
    render(<MemoryRouter><FilingItemCard filing={{
      accession: "0000000000-25-000001", ticker: "TEST", form: "8-K", filed: "2025-07-01",
      edgar_url: "https://www.sec.gov/Archives/example.htm", withheld: true, withheld_reason: null,
      withheld_kind: "gate", outcome: "withheld_gate", dropped_finding_count: 0,
      findings: [{ finding_id: "finding-1", headline: "Unverified headline", severity: "HIGH", evidence: [{
        claim_id: "claim-1", accession: "0000000000-25-000001", section_key: "item_8_01",
        char_start: 1, char_end: 20, quote: "Unverified evidence", section_sha256: "abc123",
        edgar_url: "https://www.sec.gov/Archives/example.htm",
      }] }],
    }} /></MemoryRouter>);

    expect(screen.getByText("Held back by the publication gate")).toBeInTheDocument();
    expect(screen.getByText(/did not clear verification/)).toBeInTheDocument();
    expect(screen.queryByText("Unverified headline")).not.toBeInTheDocument();
    expect(screen.queryByText("Unverified evidence")).not.toBeInTheDocument();
  });

  it("names a pipeline failure as a pipeline failure, not a gate refusal", () => {
    render(<MemoryRouter><FilingItemCard filing={{
      accession: "0000000000-25-000002", ticker: "TEST", form: "10-Q", filed: "2025-07-01",
      edgar_url: "https://www.sec.gov/Archives/example.htm", findings: [], withheld: true,
      withheld_reason: null, withheld_kind: "pipeline_failed", outcome: "pipeline_failed",
      dropped_finding_count: 0,
    }} /></MemoryRouter>);
    expect(screen.getByText("Analysis did not complete")).toBeInTheDocument();
    expect(screen.queryByText("Held back by the publication gate")).not.toBeInTheDocument();
    expect(screen.queryByText(/verification/i)).not.toBeInTheDocument();
  });

  it("reports gate-removed proposals instead of silence", () => {
    render(<MemoryRouter><FilingItemCard filing={{
      accession: "0000000000-25-000003", ticker: "TEST", form: "10-Q", filed: "2025-07-01",
      edgar_url: "https://www.sec.gov/Archives/example.htm", findings: [], withheld: false,
      withheld_reason: null, withheld_kind: null, outcome: "findings_dropped",
      dropped_finding_count: 2,
    }} /></MemoryRouter>);
    expect(screen.getByText(/2 proposed changes were removed by the evidence gate/)).toBeInTheDocument();
    expect(screen.queryByText("No evidence-backed changes were selected.")).not.toBeInTheDocument();
  });

  it("glosses every job state, including partial", () => {
    const states = [
      ["queued", "Queued"], ["running", "Running…"],
      ["completed", "Completed"], ["partial", "Finished — some items did not complete"],
      ["failed", "Failed"],
    ] as const;
    for (const [state, label] of states) {
      render(<JobProgress job={{ id: state, kind: "analysis", state, created_at: "t", items: [], error: null }} />);
      expect(screen.getByText(label)).toBeInTheDocument();
      expect(screen.queryByText(state)).not.toBeInTheDocument();
      cleanup();
    }
  });
});
