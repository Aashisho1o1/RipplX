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
  return <main className="page"><header className="page-header"><h1 className="page-title">Tracked companies</h1><div className="actions"><button className="button primary" onClick={() => navigate("?panel=add")}>Add ticker</button><button className="button" onClick={sync}>Sync filings</button></div></header>{error && <div className="notice">{error}</div>}<JobProgress job={job} />
    {!tracked.length && <div className="notice neutral">Add a ticker to start your filing brief.</div>}
    <section className="section"><h2 className="section-kicker">Tickers</h2>{tracked.length ? tracked.map(row => <CompanyRow key={row.ticker} row={row} onRemove={remove} />) : <p className="empty-line">No tickers tracked yet.</p>}</section>
    {panel === "add" && <Drawer title="Add ticker" onClose={closePanel}><AddTickerForm onAdded={() => { closePanel(); resource.refresh(); }} /></Drawer>}
  </main>;
}

function CompanyRow({ row, onRemove }: { row: TrackedCompany; onRemove: (ticker: string) => void }) {
  return <div className="holding-row"><div className="holding-row-main"><Link to={`/companies/${row.ticker}`} className="holding-row-main"><div><div className="holding-title"><strong>{row.ticker}</strong></div><div className="row-meta">{row.compressed_verified_read ?? "no verified financials yet"}</div></div><span className="row-meta">last: {row.last_filing ?? "—"}</span></Link><button className="button ghost" onClick={() => onRemove(row.ticker)}>Remove</button></div></div>;
}

function AddTickerForm({ onAdded }: { onAdded: () => void }) {
  const [ticker, setTicker] = useState(""); const [error, setError] = useState(""); const [saving, setSaving] = useState(false);
  async function submit(event: FormEvent) { event.preventDefault(); setSaving(true); setError(""); try { await api("/api/companies", { method: "POST", body: JSON.stringify({ ticker }) }); onAdded(); } catch (reason) { setError(reason instanceof ApiError ? reason.message : "Could not add this ticker."); } finally { setSaving(false); } }
  return <form className="form-grid" onSubmit={submit}><div className="field"><label htmlFor="ticker">Ticker</label><input id="ticker" className="input mono-input" required autoComplete="off" value={ticker} onChange={event => setTicker(event.target.value.toUpperCase())} /></div>{error && <div className="field-error">{error}</div>}<button className="button primary" disabled={saving}>{saving ? "Saving…" : "Add ticker"}</button></form>;
}
