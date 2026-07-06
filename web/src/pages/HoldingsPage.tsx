import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { Drawer } from "../components/Drawer";
import { JobProgress } from "../components/JobProgress";
import { OwnedWatchTag } from "../components/OwnedWatchTag";
import { PosturePill } from "../components/PosturePill";
import { SeverityBadge } from "../components/SeverityBadge";
import { useResource } from "../hooks/useResource";
import type { Holding, Holdings, Job } from "../types";

type Mode = "owned" | "watch";

export function HoldingsPage() {
  const location = useLocation(); const navigate = useNavigate(); const params = new URLSearchParams(location.search); const panel = params.get("panel"); const initialMode: Mode = params.get("mode") === "watch" ? "watch" : "owned";
  const load = useCallback((signal: AbortSignal) => api<Holdings>("/api/holdings", { signal }), []); const resource = useResource(load, []);
  const [job, setJob] = useState<Job | null>(null); const [error, setError] = useState("");
  useEffect(() => { if (!job || !["queued", "running"].includes(job.state)) return; const timer = window.setInterval(() => api<Job>(`/api/jobs/${job.id}`).then(next => { setJob(next); if (!["queued", "running"].includes(next.state)) resource.refresh(); }), 700); return () => window.clearInterval(timer); }, [job, resource.refresh]);
  function closePanel() { navigate("/holdings", { replace: true }); }
  async function sync() { setError(""); try { setJob(await api<Job>("/api/jobs/sync", { method: "POST", body: "{}" })); } catch (reason) { setError(reason instanceof ApiError ? reason.message : "Sync could not start."); } }
  async function remove(ticker: string) { if (!window.confirm(`Remove ${ticker} from your portfolio? Historical audit data will be retained.`)) return; await api(`/api/holdings/${ticker}`, { method: "DELETE" }); resource.refresh(); }
  if (!resource.data) return <main className="page">{resource.loading ? <p className="loading">Loading holdings…</p> : <div className="notice">{resource.error?.message}</div>}</main>;
  const rows = resource.data;
  return <main className="page"><header className="page-header"><h1 className="page-title">Holdings</h1><div className="actions"><button className="button primary" onClick={() => navigate("?panel=add&mode=owned")}>Add holding</button><button className="button secondary" onClick={() => navigate("?panel=add&mode=watch")}>Watch a company</button><button className="button" onClick={sync}>Sync filings</button></div></header>{error && <div className="notice">{error}</div>}<JobProgress job={job} />
    {!rows.owned.length && !rows.watching.length && <div className="notice neutral">Add a holding or watch a company to start your brief.</div>}
    <section className="section"><h2 className="section-kicker">Owned holdings</h2>{rows.owned.length ? rows.owned.map(row => <HoldingRow key={row.ticker} row={row} onRemove={remove} />) : <p className="empty-line">No owned holdings yet.</p>}</section>
    <section className="section"><h2 className="section-kicker">Watch-only</h2>{rows.watching.length ? rows.watching.map(row => <HoldingRow key={row.ticker} row={row} onRemove={remove} />) : <p className="empty-line">No watched companies yet.</p>}</section>
    {panel === "add" && <Drawer title={initialMode === "owned" ? "Add holding" : "Watch a company"} onClose={closePanel}><AddHoldingForm initialMode={initialMode} onAdded={() => { closePanel(); resource.refresh(); }} /></Drawer>}
  </main>;
}

function HoldingRow({ row, onRemove }: { row: Holding; onRemove: (ticker: string) => void }) {
  return <div className="holding-row"><div className="holding-row-main"><Link to={`/companies/${row.ticker}`} className="holding-row-main"><div><div className="holding-title"><strong>{row.ticker}</strong><OwnedWatchTag owned={row.owned} />{row.posture && <PosturePill posture={row.posture} />}{row.severity && !row.owned && <SeverityBadge severity={row.severity} />}{row.thesis && <span className="tag">THESIS</span>}</div><div className="row-meta">{row.owned ? `${row.shares?.toLocaleString() ?? "—"} shares · $${row.cost_basis?.toFixed(2) ?? "—"} cost · ${row.target_weight_pct ?? "—"}% target` : "company-level read, no signal"}</div><div className="row-meta">{row.compressed_verified_read ?? "no verified financials yet"}</div></div><span className="row-meta">last: {row.last_filing ?? "—"}</span></Link><button className="button ghost" onClick={() => onRemove(row.ticker)}>Remove</button></div></div>;
}

function AddHoldingForm({ initialMode, onAdded }: { initialMode: Mode; onAdded: () => void }) {
  const [mode, setMode] = useState(initialMode); const [ticker, setTicker] = useState(""); const [shares, setShares] = useState(""); const [cost, setCost] = useState(""); const [weight, setWeight] = useState(""); const [horizon, setHorizon] = useState(""); const [thesis, setThesis] = useState(""); const [error, setError] = useState(""); const [saving, setSaving] = useState(false);
  async function submit(event: FormEvent) { event.preventDefault(); setSaving(true); setError(""); try { await api("/api/holdings", { method: "POST", body: JSON.stringify({ ticker, owned: mode === "owned", shares: mode === "owned" ? Number(shares) : null, cost_basis: mode === "owned" ? Number(cost) : null, target_weight_pct: weight ? Number(weight) : null, horizon: horizon || null, thesis: thesis || null }) }); onAdded(); } catch (reason) { setError(reason instanceof ApiError ? reason.message : "Could not add this company."); } finally { setSaving(false); } }
  return <form className="form-grid" onSubmit={submit}><div className="segmented"><button type="button" className={mode === "owned" ? "active" : ""} onClick={() => setMode("owned")}>Add holding (I own this)</button><button type="button" className={mode === "watch" ? "active" : ""} onClick={() => setMode("watch")}>Watch</button></div>{mode === "watch" && <p className="muted">Watch = company-level read, no position, no signal.</p>}<div className="field"><label htmlFor="ticker">Ticker</label><input id="ticker" className="input mono-input" required value={ticker} onChange={event => setTicker(event.target.value.toUpperCase())} /></div>{mode === "owned" && <><div className="field"><label htmlFor="shares">Shares</label><input id="shares" className="input mono-input" type="number" min="0.000001" step="any" required value={shares} onChange={event => setShares(event.target.value)} /></div><div className="field"><label htmlFor="cost">Cost basis / share</label><input id="cost" className="input mono-input" type="number" min="0" step="any" required value={cost} onChange={event => setCost(event.target.value)} /></div><div className="divider-label">Optional</div><div className="field"><label htmlFor="weight">Target weight %</label><input id="weight" className="input mono-input" type="number" min="0" max="100" step="any" value={weight} onChange={event => setWeight(event.target.value)} /></div><div className="field"><label htmlFor="horizon">Horizon</label><select id="horizon" className="input" value={horizon} onChange={event => setHorizon(event.target.value)}><option value="">—</option><option>trading</option><option>1-3y</option><option>5y+</option><option>indefinite</option></select></div><div className="field"><label htmlFor="thesis">Thesis</label><textarea id="thesis" className="input" value={thesis} onChange={event => setThesis(event.target.value)} /><p className="helper">Optional — RipplX degrades gracefully without a thesis; you can add one later.</p></div></>}{error && <div className="field-error">{error}</div>}<button className="button primary" disabled={saving}>{saving ? "Saving…" : mode === "owned" ? "Add holding" : "Watch company"}</button></form>;
}
