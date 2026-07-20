import { Link, useLocation } from "react-router-dom";
import type { FilingDigestEntry } from "../types";
import { FindingList } from "./FindingList";

export function FilingItemCard({ filing, withholdFindings = false }: { filing: FilingDigestEntry; withholdFindings?: boolean }) {
  const location = useLocation();
  const demo = new URLSearchParams(location.search).get("demo") === "1";
  const withheld = withholdFindings || filing.withheld || Boolean(filing.withheld_reason);
  return <article className="filing-card">
    <div className="filing-heading"><div className="filing-identity"><Link className="filing-link" to={`/filings/${filing.accession}${demo ? "?demo=1" : ""}`}><strong>{filing.ticker}</strong><span aria-hidden="true">→</span></Link><span className="form-badge">{filing.form}</span><span className="mono muted">Filed {filing.filed}</span></div></div>
    {withheld ? <div className="withheld-copy compact"><strong>Held back by the publication gate</strong><p>{filing.withheld_reason ?? "No model-authored finding is shown because this filing did not clear verification."}</p></div> : filing.findings.length ? <FindingList findings={filing.findings} /> : <p className="empty-line">No evidence-backed changes were selected.</p>}
  </article>;
}
