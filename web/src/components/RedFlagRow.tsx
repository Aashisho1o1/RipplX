import type { RedFlag } from "../types";
import { SeverityBadge } from "./SeverityBadge";

export function RedFlagRow({ flag }: { flag: RedFlag }) {
  return <div className="red-flag"><div className="red-flag-head"><strong>{flag.label}</strong><SeverityBadge severity={flag.severity} />{flag.quote && <a className="citation" href={flag.edgar_url} target="_blank" rel="noopener">EDGAR ▸</a>}</div>{flag.quote && <blockquote className="quote">“{flag.quote}”</blockquote>}</div>;
}
