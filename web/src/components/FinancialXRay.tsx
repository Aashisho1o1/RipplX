import type { Metrics, RiskLens } from "../types";
import { MetricTable } from "./MetricTable";

function lensLabel(lens: string): string {
  return lens === "thesis_and_promises" ? "watch conditions" : lens.replaceAll("_", " ");
}

function RiskCard({ risk }: { risk: RiskLens }) {
  return <article className={`risk-card ${risk.status}`}>
    <div className="risk-heading"><h3>{lensLabel(risk.lens)}</h3><span>{risk.status}</span></div>
    <p>{risk.explanation}</p>
    {(risk.metric_ids.length > 0 || risk.freshness) && <small>{[...risk.metric_ids.map(id => id.replaceAll("_", " ")), risk.freshness ? `as of ${risk.freshness}` : null].filter(Boolean).join(" · ")}</small>}
  </article>;
}

export function FinancialXRay({ risks, metrics }: { risks: RiskLens[]; metrics: Metrics }) {
  const needsReview = risks.filter(row => row.status === "watch" || row.status === "elevated");
  const secondary = risks.filter(row => row.status === "stable" || row.status === "unavailable");

  return <section className="section">
    <div className="surface-header">
      <div><span className="section-kicker">03 · Financial X-Ray</span><h2>Health, downside, and verified numbers</h2></div>
      <span className="surface-meta">{needsReview.length ? `${needsReview.length} needs review` : "No flagged checks"}</span>
    </div>
    {metrics.rows?.length
      ? <div className="xray-metrics"><div className="xray-metrics-head"><strong>Verified numbers</strong><span>{metrics.summary}</span></div><MetricTable rows={metrics.rows} /></div>
      : <div className="notice neutral xray-metrics">{metrics.empty ?? "Verified metrics are not available yet. Sync SEC data to calculate them."}</div>}
    {needsReview.length > 0
      ? <div className="risk-grid compact-risk-grid">{needsReview.map(risk => <RiskCard risk={risk} key={risk.lens} />)}</div>
      : <p className="notice neutral">No available downside check currently needs review.</p>}
    {secondary.length > 0 && <details className="xray-secondary">
      <summary>Stable and unavailable checks ({secondary.length})</summary>
      <div className="risk-grid compact-risk-grid">{secondary.map(risk => <RiskCard risk={risk} key={risk.lens} />)}</div>
    </details>}
  </section>;
}
