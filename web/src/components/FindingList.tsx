import type { Finding } from "../types";
import { SeverityBadge } from "./SeverityBadge";

export function trustedSecUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && (url.hostname === "sec.gov" || url.hostname === "www.sec.gov") ? url.href : null;
  } catch {
    return null;
  }
}

export function FindingList({ findings }: { findings: Finding[] }) {
  return <div className="finding-list">{findings.map(finding => <article className="finding" key={finding.finding_id}>
    <div className="finding-heading"><h3>{finding.headline}</h3><SeverityBadge severity={finding.severity} /></div>
    <div className="finding-evidence">{finding.evidence.map(evidence => {
      const citationUrl = trustedSecUrl(evidence.edgar_url);
      return <div className="evidence" key={evidence.claim_id}>
        <blockquote className="quote">{evidence.quote}</blockquote>
        {citationUrl ? <a className="citation" href={citationUrl} target="_blank" rel="noopener noreferrer">SEC source · {evidence.section_key} · chars {evidence.char_start}–{evidence.char_end}</a> : <span className="citation faint">SEC citation unavailable</span>}
      </div>;
    })}</div>
  </article>)}</div>;
}
