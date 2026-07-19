import { useCallback } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import { DisclaimerFooter } from "../components/DisclaimerFooter";
import { FindingList, trustedSecUrl } from "../components/FindingList";
import { MetricTable } from "../components/MetricTable";
import { PosturePill } from "../components/PosturePill";
import { useResource } from "../hooks/useResource";
import type { FilingDetail } from "../types";

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
  const audit = detail.verification;
  const withheldReason = detail.withheld_reason ?? filing.withheld_reason ?? (filing.withheld ? "Findings withheld — could not be verified." : null);
  const filingUrl = trustedSecUrl(filing.edgar_url);
  const sectionValue = detail.pipeline.find(stage => stage.stage === "parse")?.diagnostics.sections_found;
  const parsedSections = Array.isArray(sectionValue) ? sectionValue.map(String) : [];

  return <main className="page">
    <button className="button ghost back-button" onClick={() => navigate(-1)}>← Back to brief</button>
    <header className="filing-detail-hero section">
      <div className="filing-detail-title"><span className="ticker-avatar large">{filing.ticker.slice(0, 2)}</span><div><p className="page-eyebrow">Filing evidence</p><h1 className="page-title">{filing.ticker} <span>· {filing.form}</span></h1><p className="page-subtitle">Filed {filing.filed} · Accession {filing.accession}</p></div></div>
      {filingUrl && <a className="button" href={filingUrl} target="_blank" rel="noopener noreferrer">Open SEC filing <span aria-hidden="true">↗</span></a>}
    </header>
    {filing.withheld && <div className="notice">⚠ Withheld — could not be verified</div>}
    {withheldReason && <div className="notice">{withheldReason}</div>}

    {!demo && <section className="surface section pipeline-surface">
      <div className="surface-header"><div><span className="section-kicker">Trust pipeline</span><h2>Publication checks</h2></div><span className="surface-meta">Five-stage audit trail</span></div>
      <div className="pipeline-list">{detail.pipeline.map((stage, index) =>
        <div className={`pipeline-stage ${stage.status}`} title={stage.error ?? undefined} key={stage.stage}>
          <span className="pipeline-index">0{index + 1}</span><span className="pipeline-state" aria-hidden="true">{stage.status === "completed" ? "✓" : stage.status === "failed" ? "!" : "·"}</span><span><strong>{stage.label}</strong><small>{stage.error ?? stage.status}</small></span>
        </div>
      )}</div>
      {parsedSections.length > 0 && <p className="mono faint">Sections: {parsedSections.join(", ")}</p>}
      {detail.research && <details className="research-audit">
        <summary>Research tools used: {detail.research.tool_call_count}</summary>
        <div className="research-audit-body">
          <p><strong>Outcome:</strong> {detail.research.outcome.replaceAll("_", " ")} · <strong>Repair:</strong> {detail.research.repair_used ? "used" : "not needed"}</p>
          {detail.research.tool_names.length > 0 && <p className="mono faint">{detail.research.tool_names.join(" · ")}</p>}
          {detail.research.dropped_findings.length > 0 && <div><strong>Dropped findings: {detail.research.dropped_findings.length}</strong>{detail.research.dropped_findings.map(row => <p className="mono faint" key={row.finding_id}>{row.finding_id}: {row.error_codes.join(", ")}</p>)}</div>}
          {detail.certificate_url && <a className="button ghost" href={`${detail.certificate_url}?download=true`}>Download verification certificate</a>}
        </div>
      </details>}
    </section>}

    <section className="surface section"><div className="surface-header"><div><span className="section-kicker">Qualitative layer</span><h2>What changed</h2></div><span className="verified-label"><i /> Evidence verified</span></div><p className="metric-caption">The model selects significance; deterministic checks prove each displayed quotation is exact. The checks do not determine whether the interpretation is important to you.</p>{withheldReason ? <p className="empty-line">Findings are withheld until deterministic verification passes.</p> : filing.findings.length ? <FindingList findings={filing.findings} /> : <p className="empty-line">No evidence-backed changes were selected.</p>}</section>
    <section className="surface section"><div className="surface-header"><div><span className="section-kicker">Deterministic layer</span><h2>Verified numbers</h2></div><Link className="button" to={`/companies/${filing.ticker}${demo ? "?demo=1" : ""}`}>Full company view <span aria-hidden="true">→</span></Link></div>{detail.verified_numbers?.empty ? <p className="empty-line">{detail.verified_numbers.empty}</p> : detail.verified_numbers ? <MetricTable rows={detail.verified_numbers.rows} /> : <p className="empty-line">No verified financials yet (XBRL facts insufficient or not yet ingested).</p>}</section>
    {!demo && <section className="section audit"><div className="surface-header"><div><span className="section-kicker">Verification audit</span><h2>Gate verdict</h2></div>{audit && <PosturePill posture={audit.verdict === "FAIL" ? "critical_review" : audit.verdict === "PASS_WITH_WARNINGS" ? "risk_review" : "monitor"} />}</div>{audit ? <div className="channels">{audit.checks.map(check => <span className="channel" key={check.check_id}>{check.check_id}: {check.verdict}</span>)}</div> : <p className="empty-line">No verification result yet.</p>}</section>}
    <DisclaimerFooter text={detail.disclaimer} />
  </main>;
}
