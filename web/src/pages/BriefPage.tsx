import { useCallback, useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { AnalysisPanel } from "../components/AnalysisPanel";
import { DisclaimerFooter } from "../components/DisclaimerFooter";
import { Drawer } from "../components/Drawer";
import { ExampleFindingSpecimen, ExampleMetricSpecimen } from "../components/ExampleSpecimen";
import { FilingItemCard } from "../components/FilingItemCard";
import { JobProgress } from "../components/JobProgress";
import { MetricTable } from "../components/MetricTable";
import { OnboardingChecklist } from "../components/OnboardingChecklist";
import { SectionHeader } from "../components/SectionHeader";
import { useBootstrap } from "../context/BootstrapContext";
import { useResource } from "../hooks/useResource";
import type { Brief, FilingType, Job } from "../types";

export function BriefPage() {
  const location = useLocation(); const navigate = useNavigate(); const { bootstrap } = useBootstrap();
  const params = new URLSearchParams(location.search); const demo = params.get("demo") === "1"; const panel = params.get("panel");
  const [job, setJob] = useState<Job | null>(null); const [actionError, setActionError] = useState("");
  const load = useCallback((signal: AbortSignal) => api<Brief>(`/api/brief?demo=${demo}`, { signal }), [demo]);
  const resource = useResource(load, [demo]);
  useEffect(() => {
    if (!job || !["queued", "running"].includes(job.state)) return;
    const timer = window.setInterval(() => api<Job>(`/api/jobs/${job.id}`).then(next => { setJob(next); if (!["queued", "running"].includes(next.state)) resource.refresh(); }).catch(reason => { setActionError(reason instanceof ApiError ? reason.message : "Job status was lost after a restart."); setJob(null); }), 700);
    return () => window.clearInterval(timer);
  }, [job, resource.refresh]);
  async function start(kind: "sync" | "analyze", formType: FilingType = "latest", ticker?: string) { setActionError(""); const payload = { ...(ticker ? { ticker } : {}), ...(formType === "latest" ? {} : { form_type: formType }) }; try { setJob(await api<Job>(`/api/jobs/${kind === "sync" ? "sync" : "analyze"}`, { method: "POST", body: JSON.stringify(payload) })); } catch (reason) { setActionError(reason instanceof ApiError ? reason.message : "Operation could not start."); } }
  function closePanel() { params.delete("panel"); navigate({ pathname: location.pathname, search: params.toString() }, { replace: true }); }
  if (!resource.data && resource.loading) return <main className="page"><p className="loading">Loading the brief…</p></main>;
  if (!resource.data) return <main className="page"><div className="notice">{resource.error?.message ?? "The brief is unavailable."}</div></main>;

  const brief = resource.data; const sample = brief.sample_data; const trackedTickers = brief.tracked_tickers;
  const showOnboarding = !sample && trackedTickers.length > 0 && brief.tracked_but_unanalyzed;
  const hasMetrics = brief.verified_numbers.some(issuer => issuer.rows.length > 0);
  const gateWithheld = brief.withheld_filings.filter(filing => filing.withheld_kind !== "pipeline_failed");
  const pipelineFailed = brief.withheld_filings.filter(filing => filing.withheld_kind === "pipeline_failed");

  return <main className="page">
    {sample && <div className="notice neutral">Sample brief · bundled public SEC filings run through the real pipeline with recorded model output. This is not your watchlist.</div>}
    {resource.error && <div className="notice">Could not refresh. Showing the last successful brief.</div>}
    <header className="brief-header"><div><p className="page-eyebrow">Your filing watch</p><h1 className="page-title">Filing brief</h1><p className="page-subtitle">A concise, evidence-backed read on what changed across the companies you follow.</p></div><div className="actions">{!sample && !showOnboarding && trackedTickers.length > 0 && <><button className="button primary" onClick={() => start("sync")}><span aria-hidden="true">↻</span> Sync filings from SEC</button><button className="button" onClick={() => navigate("?panel=analysis")}>Analyze newest filing <span aria-hidden="true">→</span></button></>}{sample && <button className="button" onClick={() => navigate("/brief")}>Exit sample</button>}</div></header>
    <section className="brief-hero"><div className="hero-copy"><span className="hero-label">Executive read</span><p className="answer-hero">{brief.answer}</p><p className="hero-note">AI-selected significance · publication gated by exact-evidence checks</p><p className="hero-note action-legend">Sync downloads new SEC filings and recomputes verified numbers — no model key needed. Analyze reads the newest supported filing for changes; RipplX never revisits older filings.</p></div><div className="brief-stats"><div><span>Reading window</span><strong className="window-label">{brief.period.covered_label}</strong><small><Link className="text-link" to="/settings">Change in Settings</Link></small></div><div><span>Filings</span><strong>{brief.period.filings_in_window} of {brief.period.filings_tracked_total}</strong><small>in the window</small></div><div><span>Published</span><strong>{brief.period.published_filings}</strong><small>{brief.period.withheld_filings ? `${brief.period.withheld_filings} held back` : "cleared the gate"}</small></div><div><span>Tracking</span><strong>{trackedTickers.length}</strong><small>{trackedTickers.length === 1 ? "company" : "companies"}</small></div></div></section>
    {brief.period.outside_window && <div className="notice neutral window-note">{brief.period.outside_window} <Link className="text-link" to="/settings">Open settings</Link></div>}
    {trackedTickers.length > 0 && <div className="ticker-strip"><span>Watchlist</span>{trackedTickers.map(ticker => <span className="ticker-chip" key={ticker}>{ticker}</span>)}</div>}
    {actionError && <div className="notice">{actionError}</div>}<JobProgress job={job} />
    {!trackedTickers.length && <section className="empty-invitation"><p className="section-kicker">Start here</p><h2>Add a company you already follow.</h2><p>RipplX will watch its newest SEC filings and publish only findings that clear the evidence gate.</p><Link className="button primary" to="/companies?panel=add">Add your first ticker</Link></section>}
    {showOnboarding && <OnboardingChecklist trackedCount={trackedTickers.length} filingsSynced={brief.filings_synced} analysisConfigured={bootstrap.analysis_configured} onSync={() => start("sync")} onAnalyze={() => navigate("?panel=analysis")} />}
    {gateWithheld.length > 0 && <section className="section withheld-section"><SectionHeader index="Held back" title="Withheld analyses" /><p className="metric-caption">The gate refused to publish model-authored content from these filings.</p>{gateWithheld.map(filing => <FilingItemCard key={filing.accession} filing={filing} withholdFindings />)}</section>}
    {pipelineFailed.length > 0 && <section className="section not-analyzed-section"><SectionHeader index="Not analyzed" title="Filings that could not be analyzed" /><p className="metric-caption">The pipeline stopped before these filings were published. This is not a verification outcome.</p>{pipelineFailed.map(filing => <FilingItemCard key={filing.accession} filing={filing} withholdFindings />)}</section>}
    {brief.gate_removed_filings.length > 0 && <section className="section"><SectionHeader index="Removed" title="Proposed changes removed by the evidence gate" /><p className="metric-caption">Every proposed change failed a deterministic evidence check; verified numbers are unaffected.</p>{brief.gate_removed_filings.map(filing => <FilingItemCard key={filing.accession} filing={filing} />)}</section>}
    {trackedTickers.length > 0 && <section className="section"><SectionHeader index="01 · Filing changes" title="What changed" /><p className="metric-caption">The model selects significance and publishes at most three findings per filing; RipplX independently checks every displayed quotation.</p>{brief.filings.length ? brief.filings.map(filing => <FilingItemCard key={filing.accession} filing={filing} />) : showOnboarding ? <ExampleFindingSpecimen /> : <div className="empty-state"><span aria-hidden="true">—</span><div><strong>No evidence-backed changes selected</strong><p>The analyzed filings were routine or did not produce a finding that cleared the gate.</p></div></div>}</section>}
    {brief.reviewed_filings.length > 0 && <section className="section"><SectionHeader index="02 · Reviewed" title="Reviewed — nothing material" /><p className="metric-caption">These filings cleared every deterministic check and legitimately produced no finding.</p>{brief.reviewed_filings.map(filing => <FilingItemCard key={filing.accession} filing={filing} />)}</section>}
    {trackedTickers.length > 0 && <section className="section"><SectionHeader index="03 · SEC XBRL" title="Verified numbers" /><p className="metric-caption">The same six metrics are computed for every issuer by versioned deterministic formulas, directly from SEC XBRL facts—never from the language model.</p>{brief.verified_numbers.map(issuer => <article className="issuer-block" key={issuer.ticker}><h3 className="issuer-title">{issuer.ticker}</h3>{issuer.empty ? <p className="empty-line">{issuer.ticker}: {issuer.empty}</p> : <MetricTable rows={issuer.rows} summary={issuer.summary} />}</article>)}{showOnboarding && !hasMetrics && <ExampleMetricSpecimen />}</section>}
    {trackedTickers.length > 0 && <section className="section"><SectionHeader index="04 · Follow-up" title="Open questions" />{brief.open_questions.length ? <ul className="question-list">{brief.open_questions.map((question, index) => <li className="muted" key={index}>{question}</li>)}</ul> : <div className="empty-state compact"><span aria-hidden="true">—</span><div><strong>No open questions</strong><p>{brief.tracked_but_unanalyzed ? "No filing has been reviewed yet, so there is nothing to follow up on." : "Nothing in this brief needs a follow-up review."}</p></div></div>}</section>}
    <DisclaimerFooter text={brief.disclaimer} />
    {panel === "analysis" && <Drawer title="Run filing analysis" onClose={closePanel}><AnalysisPanel configured={bootstrap.analysis_configured} tickers={trackedTickers} onAnalyze={(formType, ticker) => { closePanel(); start("analyze", formType, ticker); }} onConfigure={() => navigate("/settings")} onDemo={() => navigate("/brief?demo=1")} /></Drawer>}
  </main>;
}
