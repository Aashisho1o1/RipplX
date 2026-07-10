import { useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import { DisclaimerFooter } from "../components/DisclaimerFooter";
import { JobProgress } from "../components/JobProgress";
import { MetricTable } from "../components/MetricTable";
import { useBootstrap } from "../context/BootstrapContext";
import { useResource } from "../hooks/useResource";
import type { Job, Metrics } from "../types";

const disclaimer = "Educational analysis of public information for the portfolio owner's own decision-making. Not individualized investment advice. Data may be incomplete or delayed.";

export function CompanyPage() {
  const { ticker = "" } = useParams(); const location = useLocation(); const navigate = useNavigate(); const demo = new URLSearchParams(location.search).get("demo") === "1";
  const { bootstrap } = useBootstrap();
  const [asOf, setAsOf] = useState(demo ? "2024-08-05" : new Date().toISOString().slice(0, 10));
  const [job, setJob] = useState<Job | null>(null); const [jobError, setJobError] = useState("");
  const load = useCallback((signal: AbortSignal) => api<Metrics>(`/api/companies/${ticker}/metrics?as_of=${asOf}&demo=${demo}`, { signal }), [ticker, asOf, demo]);
  const resource = useResource(load, [ticker, asOf, demo]);
  useEffect(() => { if (!job || !["queued", "running"].includes(job.state)) return; const timer = window.setInterval(() => api<Job>(`/api/jobs/${job.id}`).then(next => { setJob(next); if (!["queued", "running"].includes(next.state)) resource.refresh(); }), 700); return () => window.clearInterval(timer); }, [job, resource.refresh]);
  async function start(kind: "sync" | "analyze") { setJobError(""); if (kind === "analyze" && !bootstrap.analysis_configured) { navigate("/settings"); return; } try { setJob(await api<Job>(`/api/jobs/${kind === "sync" ? "sync" : "analyze"}`, { method: "POST", body: JSON.stringify({ ticker }) })); } catch (reason) { setJobError(reason instanceof Error ? reason.message : "Operation could not start."); } }
  if (!resource.data) return <main className="page">{resource.loading ? <p className="loading">Loading verified numbers…</p> : <div className="notice">{resource.error?.message}</div>}</main>;
  const metrics = resource.data;
  return <main className="page"><button className="button ghost" onClick={() => navigate(-1)}>← Back</button><header className="page-header"><h1 className="page-title">{metrics.ticker}</h1>{!demo && <div className="actions"><button className="button" onClick={() => start("sync")}>Sync filings</button><button className="button secondary" onClick={() => start("analyze")}>Analyze latest filing</button></div>}</header>{jobError && <div className="notice">{jobError}</div>}<JobProgress job={job} /><p className="metric-caption">Computed by versioned deterministic formulas from SEC XBRL facts (never by the LLM) and traceable to those facts. ✓ = a computed value; — = not applicable or data missing.</p><div className="actions section"><label className="mono muted" htmlFor="as-of">As of</label><input id="as-of" className="input mono-input date-input" type="date" value={asOf} onChange={event => setAsOf(event.target.value)} /></div>{metrics.empty ? <div className="notice neutral">{metrics.before_first_filing ? "No filings or verified numbers existed at this date." : `${metrics.ticker}: ${metrics.empty}`}</div> : <MetricTable rows={metrics.rows} />}<DisclaimerFooter text={disclaimer} /></main>;
}
