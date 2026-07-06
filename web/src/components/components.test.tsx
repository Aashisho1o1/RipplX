import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MetricTable } from "./MetricTable";
import { PosturePill } from "./PosturePill";
import { ShadowRegion } from "./ShadowRegion";

describe("trust vocabulary", () => {
  it("renders posture values without synonyms", () => {
    render(<PosturePill posture="critical_review" />);
    expect(screen.getByText("critical_review")).toBeInTheDocument();
  });

  it("distinguishes computed, unavailable, and not-applicable metrics", () => {
    render(<MetricTable rows={[
      { metric: "Revenue growth", value: "+17.5%", formula: "revenue_growth.v1", state: "computed", state_label: "Computed from SEC XBRL facts" },
      { metric: "Leverage", value: "— data missing", formula: "simple_leverage.v1", state: "unavailable", state_label: "Data missing" },
      { metric: "EV/EBITDA", value: "— not applicable", formula: "ev_ebitda.v1", state: "not_applicable", state_label: "Not applicable for this issuer" },
    ]} />);
    expect(screen.getByLabelText("Computed from SEC XBRL facts")).toHaveTextContent("✓");
    expect(screen.getByLabelText("Data missing")).toHaveTextContent("—");
    expect(screen.getByLabelText("Not applicable for this issuer")).toHaveTextContent("—");
  });

  it("always wraps hypothetical signals in the unvalidated banner", () => {
    render(<ShadowRegion signals={[{
      ticker: "MSFT", signal: "HOLD", posture: "monitor", rules_fired: ["M8"],
      rationale: "Routine filing.", counter_evidence: "Valuation may still be stretched.",
      what_would_change_this: ["A later red flag"], rationale_withheld: false,
    }]} />);
    expect(screen.getByText(/Unvalidated shadow output/)).toBeInTheDocument();
    expect(screen.getByText("HOLD")).toBeInTheDocument();
    expect(screen.getByText(/not a trade instruction/)).toBeInTheDocument();
  });
});
