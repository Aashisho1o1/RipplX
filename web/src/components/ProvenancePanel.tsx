import { useState } from "react";
import { api, ApiError } from "../api/client";
import type { Certificate, PipelineStage, ResearchTrace } from "../types";

export const DROP_CODE_LABEL: Record<string, string> = {
  QUOTE_NOT_EXACT: "The quotation did not match the filing exactly.",
  AMBIGUOUS_QUOTE: "The quotation appeared more than once in the section.",
  NOT_A_CHANGED_SPAN: "The evidence was not in a changed passage.",
  AUTHORED_NUMBER: "The model-authored headline contained a number.",
  UNSAFE_LANGUAGE: "The headline contained advice or forbidden wording.",
  METRIC_CONTRADICTION: "The stated direction disagreed with the computed metric.",
  METRIC_DIRECTION_UNAVAILABLE: "That metric could not prove a direction.",
  FORM_SCOPE: "The filing identity did not match the requested filing.",
  CRITICAL_COVERAGE: "A required critical finding was missing.",
  HYPOTHETICAL_AS_ACTUAL: "A hypothetical disclosure was presented as an actual event.",
  TEMPORAL_MISMATCH: "The claim and its evidence referred to different periods.",
  ENTITY_MISMATCH: "The claim and its evidence referred to different entities.",
  MATERIALITY_OVERREACH: "The claim overstated what the evidence established.",
  MISSING_CHANGE_BASIS: "The claim lacked evidence from a changed passage.",
  LOW_CONFIDENCE: "The reviewer could not support the claim with enough confidence.",
};

function labelKey(value: string): string {
  return value.replaceAll("_", " ").replace(/^./, first => first.toUpperCase());
}

function printable(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function recordValue(record: Record<string, unknown>, key: string): string | null {
  const value = record[key];
  return typeof value === "string" || typeof value === "number" ? String(value) : null;
}

function shortHash(value: string | null): string {
  return value ? `${value.slice(0, 12)}…` : "—";
}

function FactGrid({ value }: { value: Record<string, unknown> }) {
  return <dl className="proof-facts">{Object.entries(value).map(([key, item]) => <div key={key}>
    <dt>{labelKey(key)}</dt><dd className="mono">{printable(item)}</dd>
  </div>)}</dl>;
}

export function ProvenancePanel({
  research,
  certificateUrl,
  pipeline,
  terminalReasonLabel,
}: {
  research: ResearchTrace | null;
  certificateUrl: string | null;
  pipeline: PipelineStage[];
  terminalReasonLabel: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [certificate, setCertificate] = useState<Certificate | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function toggleDetails() {
    const next = !expanded;
    setExpanded(next);
    if (!next || certificate || !certificateUrl || loading) return;
    setLoading(true); setError("");
    try { setCertificate(await api<Certificate>(certificateUrl)); }
    catch (reason) { setError(reason instanceof ApiError ? reason.message : "The certificate could not be loaded."); }
    finally { setLoading(false); }
  }

  if (!research && pipeline.length === 0) return null;
  const redacted = certificate?.outcome === "withheld";

  return <section className="section provenance" aria-labelledby="provenance-heading">
    <header className="proof-heading">
      <div><p className="section-kicker">Attempt-bound audit</p><h2 id="provenance-heading">How this result was checked</h2></div>
      {research && <span className={`outcome-word ${research.outcome}`}>{research.outcome.replaceAll("_", " ")}</span>}
    </header>

    {research && <div className="proof-summary">
      <dl className="proof-facts primary-proof-facts">
        <div><dt>Outcome</dt><dd>{research.outcome.replaceAll("_", " ")}</dd></div>
        <div><dt>Terminal reason</dt><dd>{terminalReasonLabel}</dd></div>
        <div><dt>Research effort</dt><dd>{research.tool_call_count} tool {research.tool_call_count === 1 ? "call" : "calls"}</dd></div>
        <div><dt>Repair</dt><dd>{research.repair_used ? "One repair used" : "No repair needed"}</dd></div>
      </dl>
      {research.tool_names.length > 0 && <p className="tool-line"><span>Tools</span><code>{research.tool_names.join(" · ")}</code></p>}
      {research.dropped_findings.length > 0 && <div className="drop-log"><p>The gate removed {research.dropped_findings.length} {research.dropped_findings.length === 1 ? "finding" : "findings"}:</p>{research.dropped_findings.map(row => <div className="drop-row" key={row.finding_id}><code>{row.finding_id}</code><span>{row.error_codes.map(code => <abbr className="code-chip" title={DROP_CODE_LABEL[code]} aria-label={DROP_CODE_LABEL[code] ? `${code}: ${DROP_CODE_LABEL[code]}` : code} key={code}>{code}</abbr>)}</span></div>)}</div>}
    </div>}

    {pipeline.length > 0 && <div className="stage-ledger" aria-label="Processing stages">{pipeline.map((stage, index) => <div className={`stage-row ${stage.status}`} key={stage.stage}>
      <span className="stage-index">{String(index + 1).padStart(2, "0")}</span>
      <span className="stage-mark" aria-hidden="true">{stage.status === "failed" ? "!" : "·"}</span>
      <span><strong>{stage.label}</strong>{stage.attempts > 1 && <small>attempt {stage.attempts} of {stage.attempts}</small>}</span>
      <span className="stage-status">{stage.error ?? stage.status}</span>
    </div>)}</div>}

    {certificateUrl && <button className="proof-toggle" type="button" aria-expanded={expanded} onClick={toggleDetails}>{expanded ? "Hide certificate details" : "Inspect certificate details"}<span aria-hidden="true">{expanded ? "−" : "+"}</span></button>}
    {expanded && <div className={`certificate-detail${redacted ? " redacted" : ""}`}>
      {loading && <p className="loading">Loading the immutable certificate…</p>}
      {error && <p className="notice">{error}</p>}
      {certificate && <>
        {redacted && <p className="redaction-note">Redacted — this attempt was not published.</p>}
        <section className="proof-block"><h3>Attempt binding</h3><p>This certificate describes one immutable analysis attempt.</p><dl className="proof-facts">
          <div><dt>P1 analysis</dt><dd className="mono">{certificate.p1_analysis_id}</dd></div>
          <div><dt>Trace analysis</dt><dd className="mono">{certificate.trace_analysis_id}</dd></div>
          <div><dt>Schema</dt><dd className="mono">{certificate.schema_version}</dd></div>
          <div><dt>Certificate SHA-256</dt><dd className="mono" title={certificate.certificate_sha256}>{shortHash(certificate.certificate_sha256)}</dd></div>
        </dl></section>
        {certificate.verification.length > 0 && <section className="proof-block"><h3>Verification checks</h3><div className="check-list">{certificate.verification.map(check => <div className={`check-row ${check.verdict.toLowerCase()}`} key={check.check_id}><code>{check.check_id}</code><span>{check.verdict}</span><small>{check.severity}</small></div>)}</div></section>}
        {certificate.evidence.length > 0 && <section className="proof-block"><h3>Evidence provenance</h3><div className="proof-table">{certificate.evidence.map((row, index) => {
          const section = recordValue(row, "section_key") ?? `Evidence ${index + 1}`;
          const start = recordValue(row, "char_start"); const end = recordValue(row, "char_end"); const hash = recordValue(row, "section_sha256");
          return <div className="evidence-proof-row" key={`${section}-${index}`}><strong>{section}</strong><code>{start && end ? `chars ${start}–${end}` : "exact span"}</code><code title={hash ?? undefined}>{shortHash(hash)}</code></div>;
        })}</div></section>}
        {certificate.metrics.length > 0 && <section className="proof-block"><h3>Metric snapshot</h3><div className="proof-table">{certificate.metrics.map((row, index) => <div className="metric-proof-row" key={`${recordValue(row, "metric_id")}-${index}`}><strong>{recordValue(row, "metric_id") ?? `Metric ${index + 1}`}</strong><code>{recordValue(row, "formula_version") ?? "—"}</code><span>{recordValue(row, "as_of") ?? "—"}</span></div>)}</div></section>}
        {(Object.keys(certificate.budgets).length > 0 || Object.keys(certificate.models).length > 0 || Object.keys(certificate.prompts).length > 0) && <section className="proof-block"><h3>Bounded run</h3><div className="proof-columns">
          {Object.keys(certificate.budgets).length > 0 && <div><h4>Budgets</h4><FactGrid value={certificate.budgets} /></div>}
          {Object.keys(certificate.models).length > 0 && <div><h4>Models</h4><FactGrid value={certificate.models} /></div>}
          {Object.keys(certificate.prompts).length > 0 && <div><h4>Prompts</h4><FactGrid value={certificate.prompts} /></div>}
        </div></section>}
        {certificate.agenda.length > 0 && <section className="proof-block"><h3>Agenda</h3><div className="check-list">{certificate.agenda.map((row, index) => <div className="check-row" key={`${recordValue(row, "name")}-${index}`}><code>{recordValue(row, "name") ?? `obligation_${index + 1}`}</code><span>{recordValue(row, "status") ?? "unknown"}</span></div>)}</div></section>}
        <a className="certificate-download" href={`${certificateUrl}?download=true`}>Download certificate (JSON) <span aria-hidden="true">↓</span></a>
      </>}
    </div>}
  </section>;
}
