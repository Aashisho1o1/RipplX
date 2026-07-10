import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MetricTable } from "./MetricTable";
import { PosturePill } from "./PosturePill";

describe("trust vocabulary", () => {
  it("renders posture values without synonyms", () => {
    render(<PosturePill posture="critical_review" />);
    expect(screen.getByText("critical_review")).toBeInTheDocument();
  });

  it("distinguishes computed, unavailable, and not-applicable metrics", () => {
    render(<MetricTable rows={[
      { metric: "Revenue growth", value: "+17.5%", formula: "revenue_growth.v1", state: "computed", state_label: "Computed from SEC XBRL facts" },
      { metric: "CFO trend", value: "— data missing", formula: "cfo_trend.v1", state: "unavailable", state_label: "Data missing" },
      { metric: "Simple leverage", value: "— not applicable", formula: "simple_leverage.v1", state: "not_applicable", state_label: "Not applicable for this issuer" },
    ]} />);
    expect(screen.getByLabelText("Computed from SEC XBRL facts")).toHaveTextContent("✓");
    expect(screen.getByLabelText("Data missing")).toHaveTextContent("—");
    expect(screen.getByLabelText("Not applicable for this issuer")).toHaveTextContent("—");
  });
});
