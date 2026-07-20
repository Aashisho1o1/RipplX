import { useCallback } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import { DisclaimerFooter } from "../components/DisclaimerFooter";
import { FindingList, trustedSecUrl } from "../components/FindingList";
import { MetricTable } from "../components/MetricTable";
import { ProvenancePanel } from "../components/ProvenancePanel";
import { useResource } from "../hooks/useResource";
import type { FilingDetail } from "../types";

export const TERMINAL_REASON_LABEL: Record<string, string> = {
  verified: "All checks passed",
  skeptic_blocked: "A reviewer objection was left unresolved",
  skeptic_incomplete: "The reviewer pass did not complete",
  budget_exhausted: "The research budget was exhausted",
  malformed_action_breakdown: "The model broke the response protocol",
  compile_failed: "A run-level check failed",
  provider_failed: "The model provider was unavailable",
  verification_failed: "A deterministic publication check failed",
  verification_incomplete: "Verification did not complete",
};

export function terminalReasonLabel(reason: string): string {
  if (TERMINAL_REASON_LABEL[reason]) return TERMINAL_REASON_LABEL[reason];
  const fallback = reason.replaceAll("_", " ").trim();
  return fallback ? fallback.replace(/^./, first => first.toUpperCase()) : "Outcome unavailable";
}

export function researchOutcomeLabel(outcome: NonNullable<FilingDetail["research"]>["outcome"]): string {
  switch (outcome) {
    case "published": return "Published with deterministic evidence checks";
    case "partial": return "Published with unsupported findings removed";
    case "metrics_only": return "Metrics published; no qualitative finding passed the gate";
    case "withheld": return "Analysis held back; no qualitative content was published";
    default: {
      const exhaustive: never = outcome;
      return exhaustive;
    }
  }
}

export function FilingPage() {
  const { accession = "" } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const demo = new URLSearchParams(location.search).get("demo") === "1";
  const load = useCallback(
    (signal: AbortSignal) => api<FilingDetail>(`/api/filings/${accession}?demo=${demo}`, { signal }),
    [accession, demo],
  );
  const resource = useResource(load, [accession, demo]);
  if (!resource.data) {
    return <main className="page">{resource.loading ? <p className="loading">Loading filing evidence…</p> : <div className="notice">{resource.error?.message}</div>}</main>;
  }

  const detail = resource.data;
  const filing = detail.filing;
  const research = detail.research;
  const withheldReason = detail.withheld_reason ?? filing.withheld_reason ?? (filing.withheld ? "Findings withheld — could not be verified." : null);
  const withheld = Boolean(withheldReason) || filing.withheld || research?.outcome === "withheld";
  const filingUrl = trustedSecUrl(filing.edgar_url);
  const reasonLabel = research ? terminalReasonLabel(research.terminal_reason) : "Analysis has not completed";
  const sectionValue = detail.pipeline.find(stage => stage.stage === "parse")?.diagnostics.sections_found;
  const parsedSections = Array.isArray(sectionValue) ? sectionValue.map(String) : [];

  return <main className="page filing-page">
    <button className="button ghost back-button" onClick={() => navigate(-1)}>← Back</button>
    <header className="filing-detail-hero section">
      <div><p className="page-eyebrow">Filing evidence</p><h1 className="page-title">{filing.ticker} <span>· {filing.form}</span></h1><p className="filing-meta">Filed {filing.filed}<span aria-hidden="true">·</span><code>{filing.accession}</code></p></div>
      {filingUrl && <a className="sec-link" href={filingUrl} target="_blank" rel="noopener noreferrer">Open SEC filing <span aria-hidden="true">↗</span></a>}
    </header>

    {research && <section className={`outcome-banner ${research.outcome}`} aria-label="Publication outcome">
      <span className="outcome-glyph" aria-hidden="true">{research.outcome === "published" ? "✓" : "!"}</span>
      <div><p>{researchOutcomeLabel(research.outcome)}</p><small>{reasonLabel}</small></div>
    </section>}
    {!research && withheldReason && <section className="outcome-banner withheld"><span className="outcome-glyph" aria-hidden="true">!</span><div><p>Analysis held back</p><small>{withheldReason}</small></div></section>}

    <section className="section reading-section" aria-labelledby="changes-heading">
      <header className="reading-heading"><div><p className="section-kicker">AI-selected interpretation</p><h2 id="changes-heading">What changed</h2></div>{!withheld && filing.findings.length > 0 && <span className="verified-label"><i aria-hidden="true" /> Exact evidence checked</span>}</header>
      <p className="metric-caption">The model selects significance. Deterministic checks prove that every displayed quotation matches the filing; they do not decide what is important to you.</p>
      {withheld ? <div className="withheld-copy"><strong>No model-authored finding is shown.</strong><p>{withheldReason ?? "This attempt did not clear the publication gate."}</p></div> : filing.findings.length ? <FindingList findings={filing.findings} /> : <p className="empty-line">No evidence-backed changes were selected. This is a legitimate routine result.</p>}
    </section>

    <section className="section reading-section" aria-labelledby="numbers-heading">
      <header className="reading-heading"><div><p className="section-kicker">Deterministic SEC XBRL</p><h2 id="numbers-heading">Verified numbers</h2></div><Link className="text-link" to={`/companies/${filing.ticker}${demo ? "?demo=1" : ""}`}>Company view <span aria-hidden="true">→</span></Link></header>
      {detail.verified_numbers?.empty ? <p className="empty-line">{detail.verified_numbers.empty}</p> : detail.verified_numbers ? <MetricTable rows={detail.verified_numbers.rows} showComputedMark={!withheld} /> : <p className="empty-line">No verified financials yet. Required XBRL facts may be unavailable or not yet ingested.</p>}
    </section>

    {parsedSections.length > 0 && <p className="parsed-sections"><span>Parsed sections</span><code>{parsedSections.join(" · ")}</code></p>}
    {!demo && <ProvenancePanel research={research} certificateUrl={detail.certificate_url} pipeline={detail.pipeline} terminalReasonLabel={reasonLabel} />}
    <DisclaimerFooter text={detail.disclaimer} />
  </main>;
}
