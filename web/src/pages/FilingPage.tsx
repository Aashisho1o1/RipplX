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
    <button className="button ghost" onClick={() => navigate(-1)}>← Back to brief</button>
    <header className="section">
      <h1 className="page-title">{filing.ticker} — {filing.form} filed {filing.filed}</h1>
      {filingUrl && <a className="citation" href={filingUrl} target="_blank" rel="noopener noreferrer">Open SEC filing ▸</a>}
    </header>
    {filing.withheld && <div className="notice">⚠ Withheld — could not be verified</div>}
    {withheldReason && <div className="notice">{withheldReason}</div>}

    {!demo && <section className="section">
      <h2 className="section-kicker">Pipeline</h2>
      <div className="channels">{detail.pipeline.map(stage =>
        <span className={`channel ${stage.status}`} title={stage.error ?? undefined} key={stage.stage}>
          {stage.label}: {stage.status}{stage.error ? ` — ${stage.error}` : ""}
        </span>
      )}</div>
      {parsedSections.length > 0 && <p className="mono faint">Sections: {parsedSections.join(", ")}</p>}
    </section>}

    <section className="section"><h2 className="section-kicker">AI-selected changes (evidence verified)</h2><p className="metric-caption">The model selects and summarizes importance. Deterministic checks prove each displayed quotation is exact; they do not prove the model's interpretation.</p>{withheldReason ? <p className="empty-line">Findings are withheld until deterministic verification passes.</p> : filing.findings.length ? <FindingList findings={filing.findings} /> : <p className="empty-line">No evidence-backed changes were selected.</p>}</section>
    <section className="section"><div className="page-header"><h2 className="section-kicker">Verified numbers</h2><Link className="button" to={`/companies/${filing.ticker}${demo ? "?demo=1" : ""}`}>Full company view</Link></div>{detail.verified_numbers?.empty ? <p className="empty-line">{detail.verified_numbers.empty}</p> : detail.verified_numbers ? <MetricTable rows={detail.verified_numbers.rows} /> : <p className="empty-line">no verified financials yet (XBRL facts insufficient or not yet ingested).</p>}</section>
    {!demo && <section className="section audit"><h2 className="section-kicker">Verification audit</h2>{audit ? <><PosturePill posture={audit.verdict === "FAIL" ? "critical_review" : audit.verdict === "PASS_WITH_WARNINGS" ? "risk_review" : "monitor"} /><div className="channels">{audit.checks.map(check => <span className="channel" key={check.check_id}>{check.check_id}: {check.verdict}</span>)}</div></> : <p className="empty-line">No verification result yet.</p>}</section>}
    <DisclaimerFooter text={detail.disclaimer} />
  </main>;
}
