import { Link, useLocation } from "react-router-dom";
import type { FilingItem } from "../types";
import { SeverityBadge } from "./SeverityBadge";

export function FilingItemCard({ filing }: { filing: FilingItem }) {
  const location = useLocation();
  const demo = new URLSearchParams(location.search).get("demo") === "1";
  return <Link className="filing-card" to={`/filings/${filing.accession}${demo ? "?demo=1" : ""}`}>
    <div className="filing-heading"><strong>{filing.ticker}</strong><span className="mono muted">{filing.form} · {filing.filed}</span>{filing.severity && <SeverityBadge severity={filing.severity} />}</div>
    <div className="material-items">{filing.material_items.map((item, index) => <span key={`${item.event_type}-${index}`}>{index > 0 ? " · " : ""}{item.headline} <em>({item.event_type})</em></span>)}</div>
  </Link>;
}
