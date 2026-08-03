import { useCallback, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useResource } from "../hooks/useResource";
import type { Companies, TrackedCompany } from "../types";

export function ResearchPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const demo = new URLSearchParams(location.search).get("demo") === "1";
  const demoSuffix = demo ? "?demo=1" : "";
  const [ticker, setTicker] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const resource = useResource(useCallback((signal: AbortSignal) => api<Companies>(`/api/companies${demo ? "?demo=true" : ""}`, { signal }), [demo]), [demo]);

  async function research(event: FormEvent) {
    event.preventDefault();
    const selected = ticker.trim().toUpperCase();
    if (!selected) return;
    setError(""); setLoading(true);
    try {
      const known = resource.data?.companies.some(row => row.ticker === selected);
      if (!known && demo) throw new ApiError("demo_company_unavailable", "That ticker is not included in the bundled demo.", 404);
      if (!known) await api<TrackedCompany>("/api/companies", { method: "POST", body: JSON.stringify({ ticker: selected }) });
      navigate(`/companies/${selected}${demoSuffix}`);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Research could not be started.");
    } finally { setLoading(false); }
  }

  return <main className="page"><header className="research-hero"><p className="page-eyebrow">Before you buy</p><h1>Understand the business before the price.</h1><p>Get a concise company brief, downside lenses, transparent valuation assumptions, and exact SEC evidence.</p><form className="ticker-search" onSubmit={research}><label className="sr-only" htmlFor="research-ticker">Ticker symbol</label><input id="research-ticker" className="input mono-input" value={ticker} onChange={event => setTicker(event.target.value.toUpperCase())} placeholder="Enter a ticker, e.g. MSFT" required /><button className="button primary" disabled={loading}>{loading ? "Starting…" : "Research company"}</button></form>{error && <div className="notice">{error}</div>}<p className="hero-note">No saved watch condition, payment, or brokerage connection required.</p></header>
    <section className="section"><div className="surface-header"><div><span className="section-kicker">Recent research</span><h2>Companies you follow</h2></div><span className="surface-meta">Verified SEC data only</span></div>{resource.data?.companies.length ? <div className="research-list">{resource.data.companies.map(row => <Link className="research-row" to={`/companies/${row.ticker}${demoSuffix}`} key={row.ticker}><span><strong>{row.ticker}</strong><small>{row.compressed_verified_read ?? "Research brief is ready to assemble"}</small></span><span>Open brief →</span></Link>)}</div> : <div className="empty-state compact"><span aria-hidden="true">—</span><div><strong>No company research yet</strong><p>Use the ticker field above to begin.</p></div></div>}</section></main>;
}
