import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { FilingItemCard } from "./FilingItemCard";
import { FilingTypePicker } from "./FilingTypePicker";
import { FindingList } from "./FindingList";
import { MetricTable } from "./MetricTable";
import { PosturePill } from "./PosturePill";

describe("trust vocabulary", () => {
  it("lets the user choose a supported filing family", () => {
    const onChange = vi.fn();
    render(<FilingTypePicker value="latest" onChange={onChange} />);

    fireEvent.click(screen.getByText("Quarterly report"));

    expect(onChange).toHaveBeenCalledWith("10-Q");
    expect(screen.getByRole("radio", { name: /Latest filing/ })).toBeChecked();
  });
  it("renders posture values without synonyms", () => {
    render(<PosturePill posture="critical_review" />);
    expect(screen.getByText("critical_review")).toBeInTheDocument();
  });

  it("distinguishes computed, unavailable, and not-applicable metrics", () => {
    render(<MetricTable rows={[
      { metric: "Revenue growth", value: "+17.5%", formula: "revenue_growth.v1", state: "computed", state_label: "Computed from SEC XBRL facts", source_computation_id: 41, effective_as_of: "2025-06-30" },
      { metric: "CFO trend", value: "— data missing", formula: "cfo_trend.v1", state: "unavailable", state_label: "Data missing", source_computation_id: 42, effective_as_of: "2025-06-30" },
      { metric: "Net debt / (operating income + D&A) proxy", value: "— not applicable", formula: "simple_leverage.v1", state: "not_applicable", state_label: "Not applicable for this issuer", source_computation_id: 43, effective_as_of: "2025-06-30" },
    ]} />);
    expect(screen.getByLabelText("Computed from SEC XBRL facts")).toHaveTextContent("✓");
    expect(screen.getByLabelText("Data missing")).toHaveTextContent("—");
    expect(screen.getByLabelText("Not applicable for this issuer")).toHaveTextContent("—");
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
    expect(screen.getByRole("link", { name: /SEC source/ })).toHaveAttribute("href", "https://www.sec.gov/Archives/example.htm");
  });

  it("withholds findings whenever a filing requires manual review", () => {
    render(<MemoryRouter><FilingItemCard filing={{
      accession: "0000000000-25-000001", ticker: "TEST", form: "8-K", filed: "2025-07-01",
      edgar_url: "https://www.sec.gov/Archives/example.htm", withheld: true, withheld_reason: null,
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
});
