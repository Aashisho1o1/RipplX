import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { Drawer } from "../components/Drawer";
import { JobProgress } from "../components/JobProgress";
import { useResource } from "../hooks/useResource";
import type { TrackedCompany, Companies, Job } from "../types";

export function CompaniesPage() {
  const location = useLocation(); const navigate = useNavigate(); const panel = new URLSearchParams(location.search).get("panel");
  const load = useCallback((signal: AbortSignal) => api<Companies>("/api/companies", { signal }), []); const resource = useResource(load, []);
  const [job, setJob] = useState<Job | null>(null); const [error, setError] = useState("");
  useEffect(() => { if (!job || !["queued", "running"].includes(job.state)) return; const timer = window.setInterval(() => api<Job>(`/api/jobs/${job.id}`).then(next => { setJob(next); if (!["queued", "running"].includes(next.state)) resource.refresh(); }).catch(reason => { setError(reason instanceof ApiError ? reason.message : "Job status was lost after a restart."); setJob(null); }), 700); return () => window.clearInterval(timer); }, [job, resource.refresh]);
  function closePanel() { navigate("/companies", { replace: true }); }
  async function sync() { setError(""); try { setJob(await api<Job>("/api/jobs/sync", { method: "POST", body: "{}" })); } catch (reason) { setError(reason instanceof ApiError ? reason.message : "Sync could not start."); } }
  async function remove(ticker: string) { if (!window.confirm(`Remove ${ticker} from tracking? Historical audit data will be retained.`)) return; setError(""); try { await api(`/api/companies/${ticker}`, { method: "DELETE" }); resource.refresh(); } catch (reason) { setError(reason instanceof ApiError ? reason.message : `Could not remove ${ticker} — it may be busy while a sync or analysis job is running. Try again in a moment.`); } }
  if (!resource.data) return <main className="page">{resource.loading ? <p className="loading">Loading companies…</p> : <div className="notice">{resource.error?.message}</div>}</main>;
  const rows = resource.data;
  const tracked = [...rows.companies].sort((left, right) => left.ticker.localeCompare(right.ticker));
  return <main className="page"><header className="page-header"><div><p className="page-eyebrow">Watchlist</p><h1 className="page-title">Tracked companies</h1><p className="page-subtitle">Choose the businesses RipplX should watch across new SEC filings.</p></div><div className="actions"><button className="button" onClick={sync}><span aria-hidden="true">↻</span> Sync filings</button>{tracked.length > 0 && <button className="button primary" onClick={() => navigate("?panel=add")}>Add ticker <span aria-hidden="true">＋</span></button>}</div></header>{error && <div className="notice">{error}</div>}<JobProgress job={job} />
    {!tracked.length ? <section className="empty-invitation company-invitation"><p className="section-kicker">Your front door</p><h2>Follow a company you already understand.</h2><p>Enter a US-listed ticker. RipplX resolves it against the SEC company index before adding it.</p><AddTickerForm onAdded={resource.refresh} introductory={false} /></section> : <section className="section"><div className="surface-header"><div><span className="section-kicker">Your watchlist</span><h2>{tracked.length} {tracked.length === 1 ? "company" : "companies"}</h2></div><span className="surface-meta">Newest 10-K/10-Q/8-K indexed per ticker</span></div><div className="company-list">{tracked.map(row => <CompanyRow key={row.ticker} row={row} onRemove={remove} />)}</div></section>}
    {panel === "add" && <Drawer title="Add ticker" onClose={closePanel}><AddTickerForm onAdded={() => { closePanel(); resource.refresh(); }} /></Drawer>}
  </main>;
}

function CompanyRow({ row, onRemove }: { row: TrackedCompany; onRemove: (ticker: string) => void }) {
  return <article className="holding-row"><Link to={`/companies/${row.ticker}`} className="holding-row-link"><div className="holding-copy"><div className="holding-title"><strong>{row.ticker}</strong><code>CIK {row.cik}</code></div><div className="row-meta">{row.compressed_verified_read ?? "No computed financials yet"}</div></div><div className="holding-last"><span>Newest 10-K/10-Q/8-K</span><strong>{row.newest_supported_filing ?? "—"}</strong></div><span className="row-arrow" aria-hidden="true">→</span></Link><button className="button ghost remove-button" onClick={() => onRemove(row.ticker)}>Remove</button></article>;
}

function AddTickerForm({ onAdded, introductory = true }: { onAdded: () => void; introductory?: boolean }) {
  const [ticker, setTicker] = useState(""); const [error, setError] = useState(""); const [saving, setSaving] = useState(false);
  async function submit(event: FormEvent) { event.preventDefault(); setSaving(true); setError(""); try { await api("/api/companies", { method: "POST", body: JSON.stringify({ ticker }) }); onAdded(); } catch (reason) { setError(reason instanceof ApiError ? reason.message : "Could not add this ticker."); } finally { setSaving(false); } }
  return <form className="form-grid" onSubmit={submit}>{introductory && <div className="analysis-intro"><div className="panel-icon" aria-hidden="true">＋</div><div><strong>Start tracking a company</strong><p>Enter a US-listed ticker. RipplX resolves it against the SEC company index before adding it.</p></div></div>}<div className="field"><label htmlFor="ticker">Ticker symbol</label><input id="ticker" className="input mono-input ticker-input" required autoComplete="off" autoFocus placeholder="e.g. MSFT" value={ticker} onChange={event => setTicker(event.target.value.toUpperCase())} /><p className="helper">You can track up to 25 companies in the hosted alpha.</p></div>{error && <div className="field-error">{error}</div>}<button className="button primary button-large" disabled={saving}>{saving ? "Adding ticker…" : "Add to watchlist"}</button></form>;
}
