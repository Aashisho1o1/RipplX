import { useCallback } from "react";
import { useLocation } from "react-router-dom";
import { DisclaimerFooter } from "../components/DisclaimerFooter";
import { useResource } from "../hooks/useResource";
import { api } from "../api/client";
import type { TrackRecord } from "../types";

const disclaimer = "Educational analysis of public information for the portfolio owner's own decision-making. Not individualized investment advice. Data may be incomplete or delayed.";

export function TrackRecordPage() {
  const location = useLocation(); const demo = new URLSearchParams(location.search).get("demo") === "1";
  const load = useCallback((signal: AbortSignal) => api<TrackRecord>(`/api/track-record?demo=${demo}`, { signal }), [demo]); const resource = useResource(load, [demo]);
  if (!resource.data) return <main className="page">{resource.loading ? <p className="loading">Loading track record…</p> : <div className="notice">{resource.error?.message}</div>}</main>;
  const data = resource.data;
  return <main className="page"><header><h1 className="page-title">Shadow-signal track record ({data.evaluations} evaluations)</h1></header><div className="notice section">Signals are UNVALIDATED educational shadow output. Promotion requires ≥100 logged evaluations, a human audit of ≥20 sampled cases, and passing the acceptance gates.</div>{data.evaluations === 0 && <p className="empty-line">No evaluations logged yet — shadow signals accrue as owned holdings are analyzed.</p>}<section className="section"><h2 className="section-kicker">Review postures</h2><div className="count-chips">{Object.entries(data.posture_counts).map(([name, count]) => <span className="count-chip" key={name}>{name}={count}</span>)}</div></section><section className="section"><h2 className="section-kicker">Hypothetical signals</h2><div className="count-chips">{Object.entries(data.signal_counts).map(([name, count]) => <span className="count-chip" key={name}>{name}={count}</span>)}</div></section><p className="mono faint">Outcomes reviewed: {data.outcomes_reviewed}/{data.evaluations}</p><DisclaimerFooter text={disclaimer} /></main>;
}
