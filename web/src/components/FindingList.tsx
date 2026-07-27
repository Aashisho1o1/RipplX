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
  return <div className="finding-list">{findings.map((finding, position) => <article className="finding" key={finding.finding_id}>
    {/* The index is positional, not model-authored: it gives the eye an anchor and makes
        the at-most-three cap legible without adding anything to the finding contract. */}
    <div className="finding-heading"><h3><span className="finding-index" aria-hidden="true">{position + 1}</span>{finding.headline}</h3><SeverityBadge severity={finding.severity} /></div>
    <div className="finding-evidence">{finding.evidence.map(evidence => {
      const citationUrl = trustedSecUrl(evidence.edgar_url);
      return <div className="evidence" key={evidence.claim_id}>
        <blockquote className="quote">{evidence.quote}</blockquote>
        <p className="citation-line"><span className="citation-meta">{evidence.section_key} · chars {evidence.char_start}–{evidence.char_end} · <span title={evidence.section_sha256}>{evidence.section_sha256.slice(0, 12)}…</span></span>{citationUrl ? <a className="citation" href={citationUrl} target="_blank" rel="noopener noreferrer">View filing on EDGAR ↗</a> : <span className="citation faint">SEC citation unavailable</span>}</p>
      </div>;
    })}</div>
  </article>)}</div>;
}
