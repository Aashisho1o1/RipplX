import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, readPath } from "../api/client";
import { AnalysisPanel } from "../components/AnalysisPanel";
import { DeepResearchPanel } from "../components/DeepResearchPanel";
import { DisclaimerFooter } from "../components/DisclaimerFooter";
import { Drawer } from "../components/Drawer";
import { FinancialXRay } from "../components/FinancialXRay";
import { JobProgress } from "../components/JobProgress";
import { useBootstrap } from "../context/BootstrapContext";
import { useResource } from "../hooks/useResource";
import type { CompanyResearch, FilingType, Job, ProductProfile, ResearchRun, Thesis, ThesisStatus } from "../types";

const thesisStatuses: ThesisStatus[] = ["draft", "confirmed", "supported", "weakened", "broken", "unclear", "retired"];
interface CompanyRouteState { initialJob?: Job; autoResearch?: boolean; notice?: string }

export function CompanyPage() {
  const { ticker = "" } = useParams(); const location = useLocation(); const navigate = useNavigate();
  const routeState = location.state as CompanyRouteState | null;
  const demo = new URLSearchParams(location.search).get("demo") === "1"; const panel = new URLSearchParams(location.search).get("panel");
  const demoSuffix = demo ? "?demo=1" : "";
  const { bootstrap } = useBootstrap();
  const load = useCallback((signal: AbortSignal) => api<CompanyResearch>(readPath(demo, `companies/${ticker}/research`), { signal }), [ticker, demo]);
  const resource = useResource(load, [ticker, demo]);
  const [job, setJob] = useState<Job | null>(routeState?.initialJob ?? null); const [error, setError] = useState(routeState?.notice ?? "");
  const [autoResearch, setAutoResearch] = useState(Boolean(routeState?.autoResearch));
  const [thesis, setThesis] = useState<Thesis>({ items: [] }); const [savingThesis, setSavingThesis] = useState(false);
  const [profile, setProfile] = useState<ProductProfile | null>(null); const [manualPeers, setManualPeers] = useState<string[]>([]); const [peerInput, setPeerInput] = useState("");
  const [deepResearch, setDeepResearch] = useState<ResearchRun | null>(null);
  useEffect(() => { if (resource.data) { setThesis(resource.data.thesis); setProfile(resource.data.profile); setManualPeers(resource.data.manual_peer_tickers); setDeepResearch(resource.data.deep_research); } }, [resource.data]);
  useEffect(() => { if (!job || !["queued", "running"].includes(job.state)) return; const timer = window.setInterval(() => api<Job>(`/api/jobs/${job.id}`).then(next => { setJob(next); if (!["queued", "running"].includes(next.state)) { resource.refresh(); if (autoResearch && next.kind === "analysis" && next.state !== "failed") { setAutoResearch(false); api<ResearchRun>(`/api/companies/${ticker}/research-runs`, { method: "POST" }).then(setDeepResearch).catch(reason => setError(reason instanceof ApiError ? reason.message : "Connected research could not start.")); } } }).catch(() => setError("Job status was lost after a restart.")), 700); return () => window.clearInterval(timer); }, [job, resource.refresh, autoResearch, ticker]);
  useEffect(() => { if (!deepResearch || !["queued", "running"].includes(deepResearch.status)) return; const timer = window.setInterval(() => api<ResearchRun>(`/api/research-runs/${deepResearch.run_id}`).then(next => { setDeepResearch(next); if (!["queued", "running"].includes(next.status)) resource.refresh(); }).catch(() => setError("Research status was lost after a restart.")), 900); return () => window.clearInterval(timer); }, [deepResearch, resource.refresh]);

  async function start(kind: "sync" | "analyze", formType: FilingType = "latest") { setError(""); try { setJob(await api<Job>(`/api/jobs/${kind}`, { method: "POST", body: JSON.stringify({ ticker, ...(formType === "latest" ? {} : { form_type: formType }) }) })); } catch (reason) { setError(reason instanceof ApiError ? reason.message : "Operation could not start."); } }
  async function draftThesis() { setError(""); try { setThesis(await api<Thesis>(`/api/companies/${ticker}/thesis/draft`, { method: "POST" })); } catch (reason) { setError(reason instanceof ApiError ? reason.message : "A thesis draft could not be created."); } }
  async function saveThesis() { setSavingThesis(true); setError(""); try { setThesis(await api<Thesis>(`/api/companies/${ticker}/thesis`, { method: "PUT", body: JSON.stringify({ thesis }) })); } catch (reason) { setError(reason instanceof ApiError ? reason.message : "The thesis could not be saved."); } finally { setSavingThesis(false); } }
  async function saveProfile(next: ProductProfile | null = profile, peers = manualPeers) { if (!next) return; setError(""); try { const saved = await api<ProductProfile>(`/api/companies/${ticker}/profile`, { method: "PUT", body: JSON.stringify({ monitoring_enabled: next.monitoring_enabled, notification_level: next.notification_level, peer_tickers: peers }) }); setProfile(saved); setManualPeers(peers); } catch (reason) { setError(reason instanceof ApiError ? reason.message : "Monitoring preferences could not be saved."); } }
  async function addPeer(event: FormEvent) { event.preventDefault(); const selected = peerInput.trim().toUpperCase(); if (!selected || manualPeers.includes(selected) || selected === ticker.toUpperCase()) return; const next = [...manualPeers, selected].slice(0, 6); await saveProfile(profile, next); setPeerInput(""); resource.refresh(); }
  async function removePeer(selected: string) { const next = manualPeers.filter(row => row !== selected); await saveProfile(profile, next); resource.refresh(); }
  async function runDeepResearch() { setError(""); try { setDeepResearch(await api<ResearchRun>(`/api/companies/${ticker}/research-runs`, { method: "POST" })); } catch (reason) { setError(reason instanceof ApiError ? reason.message : "Deep research could not start."); } }
  function updateThesis(index: number, patch: Partial<Thesis["items"][number]>) { setThesis(current => ({ items: current.items.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item) })); }
  if (!resource.data) return <main className="page">{resource.loading ? <p className="loading">Assembling the company brief…</p> : <div className="notice">{resource.error?.message}</div>}</main>;
  const brief = resource.data;
  const connectedInsight = deepResearch?.report?.insights[0] ?? null;
  const leadingChange = brief.impact.changes[0] ?? null;
  const priorityRisk = brief.risks.find(row => row.status === "elevated") ?? brief.risks.find(row => row.status === "watch") ?? null;
  const attentionTitle = connectedInsight?.headline ?? leadingChange?.headline ?? (priorityRisk ? `${priorityRisk.lens.replaceAll("_", " ")} needs attention` : "No material deterioration detected");
  const attentionBody = connectedInsight?.implication ?? leadingChange?.implication ?? priorityRisk?.explanation ?? brief.metrics.summary ?? "Review the verified numbers and evidence below.";
  return <main className="page"><button className="button ghost back-button" onClick={() => navigate(-1)}>← Back</button>
    <header className="company-brief-header"><div><p className="page-eyebrow">Stock decision brief · as of {brief.as_of}</p><h1>{brief.ticker}<small>{brief.company_name}</small></h1><p>See what changed, how the verified numbers connect, and where downside needs attention.</p>{brief.business_summary && <p className="business-summary-inline">{brief.business_summary}</p>}</div>{!demo && <div className="actions"><button className="button" onClick={() => start("sync")}>Sync filings</button><button className="button primary" onClick={() => navigate("?panel=analysis")}>Analyze filing</button></div>}</header>
    {error && <div className="notice">{error}</div>}<JobProgress job={job} />
    {profile && !demo && <section className="monitoring-bar"><label><input type="checkbox" checked={profile.monitoring_enabled} onChange={event => { const next = { ...profile, monitoring_enabled: event.target.checked }; setProfile(next); void saveProfile(next); }} /> Monitor new SEC filings</label><label>Notify me <select className="input" value={profile.notification_level} onChange={event => { const next = { ...profile, notification_level: event.target.value as ProductProfile["notification_level"] }; setProfile(next); void saveProfile(next); }} disabled={!profile.monitoring_enabled}><option value="urgent">for urgent changes</option><option value="this_week">for urgent and this-week changes</option><option value="weekly">in the weekly brief only</option><option value="off">in-app only</option></select></label></section>}
    <section className="attention-panel"><div><span className="section-kicker">01 · What deserves attention</span><h2>{attentionTitle}</h2><p>{attentionBody}</p></div>{brief.recent_filings[0] && <Link className="button" to={`/filings/${brief.recent_filings[0].accession}${demoSuffix}`}>Review evidence</Link>}</section>

    <DeepResearchPanel run={deepResearch} canRun={bootstrap.analysis_configured && brief.recent_filings.some(row => row.outcome === "published" || row.outcome === "no_findings" || row.outcome === "findings_dropped")} demo={demo} fallbackChanges={brief.impact.changes} onRun={() => void runDeepResearch()} />

    <FinancialXRay risks={brief.risks} metrics={brief.metrics} />

    <section className="section"><div className="surface-header"><div><span className="section-kicker">04 · Monitor what matters</span><h2>Saved watch conditions</h2></div>{!demo && (thesis.items.length ? <button className="button" disabled={savingThesis} onClick={saveThesis}>{savingThesis ? "Saving…" : "Save conditions"}</button> : <button className="button" onClick={draftThesis}>Draft from verified evidence</button>)}</div>{thesis.items.length ? <div className="thesis-list">{thesis.items.slice(0, 5).map((item, index) => <article className="thesis-item" key={item.item_id}><span>{item.kind.replaceAll("_", " ")}</span><textarea className="input" value={item.text} onChange={event => updateThesis(index, { text: event.target.value })} disabled={demo} /><select className="input" value={item.status} onChange={event => updateThesis(index, { status: event.target.value as ThesisStatus })} disabled={demo}>{thesisStatuses.map(status => <option key={status}>{status}</option>)}</select></article>)}</div> : <div className="empty-state compact"><span aria-hidden="true">—</span><div><strong>No saved conditions yet</strong><p>Filing and downside monitoring still runs without them.</p></div></div>}{brief.promises.length > 0 && <details className="watch-promises"><summary>Additional filing commitments to watch</summary>{brief.promises.map(row => <blockquote className="promise" key={row.promise_id}>{row.quote}<footer>{row.status} · {row.accession}</footer></blockquote>)}</details>}</section>

    <details className="section more-research"><summary>Comparisons and verification</summary><div><span className="section-kicker">Similar companies</span><h2>Compare the next business</h2>{brief.peers.length ? brief.peers.map(peer => <article className="peer-row" key={peer.ticker}><div><strong>{peer.ticker}</strong><small>{peer.reason}</small></div><div className="actions"><Link className="button ghost" to={`/companies/${peer.ticker}${demoSuffix}`}>Research</Link>{manualPeers.includes(peer.ticker) && !demo && <button className="button ghost" onClick={() => removePeer(peer.ticker)}>Remove</button>}</div></article>) : <p className="muted">No comparable company with verified local data yet.</p>}{!demo && <form className="peer-form" onSubmit={addPeer}><input className="input mono-input" value={peerInput} onChange={event => setPeerInput(event.target.value.toUpperCase())} placeholder="Add ticker" maxLength={15} /><button className="button" disabled={!peerInput.trim() || manualPeers.length >= 6}>Add peer</button></form>}<div className="certificate-links">{brief.certificate_urls.map((url, index) => <a className="button ghost" href={`${url}?download=true${demo ? "&demo=true" : ""}`} key={url}>Certificate {index + 1}</a>)}</div></div></details>
    <DisclaimerFooter text={brief.disclaimer} />
    {panel === "analysis" && <Drawer title={`Analyze ${brief.ticker}`} onClose={() => navigate(`/companies/${ticker}`, { replace: true })}><AnalysisPanel configured={bootstrap.analysis_configured} onAnalyze={formType => { navigate(`/companies/${ticker}`, { replace: true }); start("analyze", formType); }} /></Drawer>}
  </main>;
}
