import { useCallback } from "react";
import { Link, useLocation } from "react-router-dom";
import { api } from "../api/client";
import { useResource } from "../hooks/useResource";
import type { Alerts } from "../types";

export function AlertsPage() {
  const location = useLocation();
  const demoSuffix = new URLSearchParams(location.search).get("demo") === "1" ? "?demo=1" : "";
  const resource = useResource(useCallback((signal: AbortSignal) => api<Alerts>("/api/alerts", { signal }), []), []);
  async function markRead(eventId: number | null) { if (!eventId) return; await api<void>(`/api/alerts/${eventId}/read`, { method: "PUT" }); resource.refresh(); }
  if (!resource.data) return <main className="page"><p className={resource.loading ? "loading" : "notice"}>{resource.loading ? "Loading monitoring history…" : resource.error?.message}</p></main>;
  return <main className="page"><header className="page-header"><div><p className="page-eyebrow">Monitoring history</p><h1 className="page-title">Alerts</h1><p className="page-subtitle">A chronological record of verified filing attention—not a stream of market noise.</p></div></header>{resource.data.events.length ? <section className="alert-timeline">{resource.data.events.map(event => <article className={`alert-row ${event.priority}${event.read_at ? " read" : ""}`} key={event.event_key}><div className="alert-priority">{event.priority.replace("_", " ")}</div><div><h2>{event.ticker}</h2><p>{event.reason_codes.map(code => code.replaceAll("_", " ").toLowerCase()).join(" · ")}</p><small>{new Date(event.created_at).toLocaleString()} {event.accession ? `· ${event.accession}` : ""}</small></div><div className="actions"><Link className="button ghost" to={`/companies/${event.ticker}${demoSuffix}`}>Review</Link>{!event.read_at && <button className="button ghost" onClick={() => markRead(event.event_id)}>Mark read</button>}</div></article>)}</section> : <section className="empty-invitation"><p className="section-kicker">All quiet</p><h2>No monitoring events yet.</h2><p>Research a company and sync its filings. RipplX records routine cycles too, so silence is explicit.</p><Link className="button primary" to={`/research${demoSuffix}`}>Research a company</Link></section>}</main>;
}
