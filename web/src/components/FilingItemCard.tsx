import { Link, useLocation } from "react-router-dom";
import type { FilingDigestEntry } from "../types";
import { FindingList } from "./FindingList";

export function FilingItemCard({ filing, withholdFindings = false }: { filing: FilingDigestEntry; withholdFindings?: boolean }) {
  const location = useLocation();
  const demo = new URLSearchParams(location.search).get("demo") === "1";
  const withheld = withholdFindings || filing.manual_review || Boolean(filing.withheld_reason);
  return <article className="filing-card">
    <div className="filing-heading"><Link className="filing-link" to={`/filings/${filing.accession}${demo ? "?demo=1" : ""}`}><strong>{filing.ticker}</strong></Link><span className="mono muted">{filing.form} · {filing.filed}</span></div>
    {withheld ? <p className="notice">{filing.withheld_reason ?? "Findings withheld pending manual review."}</p> : filing.findings.length ? <FindingList findings={filing.findings} /> : <p className="empty-line">No evidence-backed changes were selected.</p>}
  </article>;
}
