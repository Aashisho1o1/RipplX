import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Certificate, PipelineStage, ResearchTrace } from "../types";
import { ProvenancePanel } from "./ProvenancePanel";

const research: ResearchTrace = {
  outcome: "withheld",
  terminal_reason: "verification_failed",
  tool_call_count: 1,
  tool_names: ["search_sections"],
  repair_used: false,
  dropped_findings: [{ finding_id: "f1", error_codes: ["LOW_CONFIDENCE"] }],
};

const pipeline: PipelineStage[] = [
  { stage: "verify", label: "Verify publication", status: "completed", attempts: 1, error: null, diagnostics: {} },
];

const redactedCertificate: Certificate = {
  schema_version: "certificate.v2",
  certificate_sha256: "a".repeat(64),
  p1_analysis_id: 21,
  trace_analysis_id: 22,
  p1_output_sha256: "b".repeat(64),
  filing: { accession: "a-1" },
  outcome: "withheld",
  terminal_reason: "verification_failed",
  published_finding_ids: [],
  dropped_findings: research.dropped_findings,
  classification: null,
  evidence: [],
  metrics: [],
  verification: [{ check_id: "V5", verdict: "FAIL", severity: "blocking", detail: null }],
  tool_calls: [{ call_id: "t1", tool: "search_sections", result_sha256: "c".repeat(64) }],
  agenda: [{ name: "FORM_SCOPE", status: "discharged" }],
  models: { generator: "model-a" },
  prompts: { generator: "P1.v2" },
  budgets: { generator_turns: 2, tool_budget: 6 },
};

afterEach(() => vi.unstubAllGlobals());

describe("ProvenancePanel", () => {
  it("loads a certificate only after expansion and renders a redacted attempt without leakage", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(redactedCertificate), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetch);
    render(<ProvenancePanel research={research} certificateUrl="/api/filings/a-1/certificate" pipeline={pipeline} terminalReasonLabel="A deterministic publication check failed" />);

    expect(fetch).not.toHaveBeenCalled();
    expect(screen.getByText("LOW_CONFIDENCE")).toHaveAttribute("title", "The reviewer could not support the claim with enough confidence.");
    fireEvent.click(screen.getByRole("button", { name: "Inspect certificate details" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Redacted — this attempt was not published.")).toBeInTheDocument();
    expect(screen.getByText("certificate.v2")).toBeInTheDocument();
    expect(screen.getByText("V5")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Models" })).toBeInTheDocument();
    expect(screen.getByText("model-a")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Prompts" })).toBeInTheDocument();
    expect(screen.getByText("P1.v2")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Evidence provenance" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Metric snapshot" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Download certificate/ })).toHaveAttribute("href", "/api/filings/a-1/certificate?download=true");
  });
});

describe("stage failure reasons", () => {
  it("names why a stage failed, and ignores a reason outside the allowlist", () => {
    const stages: PipelineStage[] = [
      { stage: "extract", label: "Research changes", status: "failed", attempts: 2, error: "Stage failed; details are withheld.", diagnostics: { reason: "provider_failed" } },
      { stage: "verify", label: "Verify publication", status: "failed", attempts: 1, error: "Stage failed; details are withheld.", diagnostics: { reason: "sk-live-leaked-secret" } },
    ];
    render(<ProvenancePanel research={null} certificateUrl={null} pipeline={stages} terminalReasonLabel="—" />);

    expect(screen.getByText("The model provider could not be reached or rejected the request.")).toBeInTheDocument();
    // An unrecognised reason renders nothing rather than being echoed into the page.
    expect(screen.queryByText(/sk-live-leaked-secret/)).toBeNull();
  });
});
