import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { Verification } from "../types";
import { VerificationBand } from "./VerificationBand";

const verification: Verification = {
  verdict: "PASS_WITH_WARNINGS",
  checks: [
    { check_id: "V1", verdict: "PASS", severity: "blocking", detail: null },
    { check_id: "V4", verdict: "PASS", severity: "blocking", detail: null },
    { check_id: "V5", verdict: "PASS", severity: "blocking", detail: null },
    { check_id: "V2a", verdict: "WARN", severity: "warning", detail: "A=100.0 L+E=110.0" },
    { check_id: "V2c", verdict: "SKIPPED_NOT_APPLICABLE", severity: "info", detail: null },
    { check_id: "V9z", verdict: "PASS", severity: "info", detail: null },
  ],
};

afterEach(cleanup);

describe("deterministic verification band", () => {
  it("renders every persisted check with its machine id, human label, and verdict", () => {
    render(<VerificationBand verification={verification} />);
    for (const code of ["V1", "V4", "V5", "V2a", "V2c", "V9z"]) {
      expect(screen.getAllByText(code).length).toBeGreaterThan(0);
    }
    expect(screen.getByText(/Every number shown traces/)).toBeInTheDocument();
    expect(screen.getByText("Not applicable")).toBeInTheDocument();
    expect(screen.getByText("Passed with data-quality warnings")).toBeInTheDocument();
    expect(screen.getByText("Other recorded checks").closest(".check-group")).toHaveTextContent("V9z");
  });

  it("separates the blocking gate from non-blocking data quality and escapes all text", () => {
    const { container } = render(<VerificationBand verification={verification} />);
    const gate = screen.getByText("Blocking — a failure withholds the finding").closest(".check-group");
    const quality = screen.getByText("Non-blocking — reported, never a gate").closest(".check-group");
    expect(gate).toHaveTextContent("V1");
    expect(gate).toHaveTextContent("V4");
    expect(gate).toHaveTextContent("V5");
    expect(gate?.querySelector(".check-detail")).toBeNull();
    expect(quality).toHaveTextContent("V2a");
    expect(quality).toHaveTextContent("V2c");
    expect(quality).toHaveTextContent("A=100.0 L+E=110.0");

    cleanup();
    const escaped = render(<VerificationBand verification={{ ...verification, checks: [{
      check_id: "V2a", verdict: "WARN", severity: "warning", detail: "<b>x</b>",
    }] }} />);
    expect(escaped.container.querySelector("b")).toBeNull();
    expect(screen.getByText("<b>x</b>")).toBeInTheDocument();
  });

  it("renders nothing when there is no verification or no checks", () => {
    const { container, rerender } = render(<VerificationBand verification={null} />);
    expect(container.firstChild).toBeNull();
    rerender(<VerificationBand verification={{ verdict: "PASS", checks: [] }} />);
    expect(container.firstChild).toBeNull();
  });
});
