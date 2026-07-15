import { Link, useLocation } from "react-router-dom";
import type { FilingDigestEntry } from "../types";
import { FindingList } from "./FindingList";

export function FilingItemCard({ filing, withholdFindings = false }: { filing: FilingDigestEntry; withholdFindings?: boolean }) {
  const location = useLocation();
  const demo = new URLSearchParams(location.search).get("demo") === "1";
  const withheld = withholdFindings || filing.withheld || Boolean(filing.withheld_reason);
  return <article className="filing-card">
    <div className="filing-heading"><div className="filing-identity"><span className="ticker-avatar">{filing.ticker.slice(0, 2)}</span><div><Link className="filing-link" to={`/filings/${filing.accession}${demo ? "?demo=1" : ""}`}><strong>{filing.ticker}</strong><span aria-hidden="true">↗</span></Link><span className="mono muted">Filed {filing.filed}</span></div></div><span className="form-badge">{filing.form}</span></div>
    {withheld ? <p className="notice">{filing.withheld_reason ?? "Findings withheld — could not be verified."}</p> : filing.findings.length ? <FindingList findings={filing.findings} /> : <p className="empty-line">No evidence-backed changes were selected.</p>}
  </article>;
}
