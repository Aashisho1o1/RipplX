import { useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { DisclaimerFooter } from "../components/DisclaimerFooter";
import { AnalysisPanel } from "../components/AnalysisPanel";
import { Drawer } from "../components/Drawer";
import { JobProgress } from "../components/JobProgress";
import { MetricTable } from "../components/MetricTable";
import { useBootstrap } from "../context/BootstrapContext";
import { useResource } from "../hooks/useResource";
import type { FilingType, Job, Metrics } from "../types";

const disclaimer = "Educational analysis of public information for the portfolio owner's own decision-making. Not individualized investment advice. Data may be incomplete or delayed.";

export function CompanyPage() {
  const { ticker = "" } = useParams(); const location = useLocation(); const navigate = useNavigate(); const demo = new URLSearchParams(location.search).get("demo") === "1";
  const panel = new URLSearchParams(location.search).get("panel");
  const { bootstrap } = useBootstrap();
  const [asOf, setAsOf] = useState(demo ? "2024-08-05" : new Date().toISOString().slice(0, 10));
  const [job, setJob] = useState<Job | null>(null); const [jobError, setJobError] = useState("");
  const load = useCallback((signal: AbortSignal) => api<Metrics>(`/api/companies/${ticker}/metrics?as_of=${asOf}&demo=${demo}`, { signal }), [ticker, asOf, demo]);
  const resource = useResource(load, [ticker, asOf, demo]);
  useEffect(() => { if (!job || !["queued", "running"].includes(job.state)) return; const timer = window.setInterval(() => api<Job>(`/api/jobs/${job.id}`).then(next => { setJob(next); if (!["queued", "running"].includes(next.state)) resource.refresh(); }).catch(reason => { setJobError(reason instanceof ApiError ? reason.message : "Job status was lost after a restart."); setJob(null); }), 700); return () => window.clearInterval(timer); }, [job, resource.refresh]);
  async function start(kind: "sync" | "analyze", formType: FilingType = "latest") { setJobError(""); const payload = { ticker, ...(formType === "latest" ? {} : { form_type: formType }) }; try { setJob(await api<Job>(`/api/jobs/${kind === "sync" ? "sync" : "analyze"}`, { method: "POST", body: JSON.stringify(payload) })); } catch (reason) { setJobError(reason instanceof Error ? reason.message : "Operation could not start."); } }
  function closePanel() { navigate(`/companies/${ticker}`, { replace: true }); }
  if (!resource.data) return <main className="page">{resource.loading ? <p className="loading">Loading verified numbers…</p> : <div className="notice">{resource.error?.message}</div>}</main>;
  const metrics = resource.data;
  return <main className="page"><button className="button ghost back-button" onClick={() => navigate(-1)}>← Back to companies</button><header className="page-header company-header"><div><p className="page-eyebrow">Company intelligence</p><h1 className="page-title">{metrics.ticker}</h1><p className="page-subtitle">Deterministic filing metrics and evidence-gated analysis for {metrics.ticker}.</p></div>{!demo && <div className="actions"><button className="button" onClick={() => start("sync")}>Sync filings</button><button className="button primary" onClick={() => navigate(`?panel=analysis`)}>Analyze a filing</button></div>}</header>{jobError && <div className="notice">{jobError}</div>}<JobProgress job={job} /><section className="section reading-section"><div className="surface-header"><div><span className="section-kicker">SEC XBRL</span><h2>Verified numbers</h2></div><div className="date-control"><label className="mono muted" htmlFor="as-of">Computed as of</label><input id="as-of" className="input mono-input date-input" type="date" value={asOf} onChange={event => setAsOf(event.target.value)} /></div></div><p className="metric-caption">Versioned deterministic formulas compute every value directly from SEC XBRL facts—never from the language model.</p>{metrics.empty ? <div className="notice neutral">{metrics.before_first_filing ? "No filings or computed numbers existed at this date." : `${metrics.ticker}: ${metrics.empty}`}</div> : <MetricTable rows={metrics.rows} />}</section><DisclaimerFooter text={disclaimer} />{panel === "analysis" && <Drawer title={`Analyze ${metrics.ticker}`} onClose={closePanel}><AnalysisPanel configured={bootstrap.analysis_configured} onAnalyze={formType => { closePanel(); start("analyze", formType); }} onConfigure={() => navigate("/settings")} /></Drawer>}</main>;
}
