import { useCallback, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError, readPath } from "../api/client";
import { JobProgress } from "../components/JobProgress";
import { useBootstrap } from "../context/BootstrapContext";
import { useResource } from "../hooks/useResource";
import type { Companies, Job, TrackedCompany } from "../types";

const TERMINAL_JOB_STATES = new Set<Job["state"]>(["completed", "partial", "failed"]);

async function waitForJob(started: Job, onUpdate: (job: Job) => void): Promise<Job> {
  let current = started;
  for (let poll = 0; poll < 180; poll += 1) {
    onUpdate(current);
    if (TERMINAL_JOB_STATES.has(current.state)) return current;
    await new Promise(resolve => window.setTimeout(resolve, 500));
    current = await api<Job>(`/api/jobs/${started.id}`);
  }
  throw new ApiError(
    "job_timeout",
    "SEC research is still running. Open the company page again in a moment.",
    408,
  );
}

export function ResearchPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { bootstrap } = useBootstrap();
  const demo = new URLSearchParams(location.search).get("demo") === "1";
  const demoSuffix = demo ? "?demo=1" : "";
  const [ticker, setTicker] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const resource = useResource(
    useCallback(
      (signal: AbortSignal) => api<Companies>(readPath(demo, "companies"), { signal }),
      [demo],
    ),
    [demo],
  );

  async function research(event: FormEvent) {
    event.preventDefault();
    const selected = ticker.trim().toUpperCase();
    if (!selected) return;
    setError("");
    setJob(null);
    setLoading(true);
    try {
      const known = resource.data?.companies.find(row => row.ticker === selected);
      if (demo) {
        if (!known) {
          throw new ApiError(
            "demo_company_unavailable",
            "That ticker is not included in the public SEC showcase.",
            404,
          );
        }
        navigate(`/companies/${known.ticker}${demoSuffix}`);
        return;
      }

      setProgress(known ? `Refreshing ${selected} from SEC…` : `Adding ${selected}…`);
      const company = known ?? await api<TrackedCompany>("/api/companies", {
        method: "POST",
        body: JSON.stringify({ ticker: selected }),
      });
      const resolvedTicker = company.ticker;

      setProgress("Syncing SEC filings and verified numbers…");
      const startedSync = await api<Job>("/api/jobs/sync", {
        method: "POST",
        body: JSON.stringify({ ticker: resolvedTicker }),
      });
      const synced = await waitForJob(startedSync, setJob);
      if (synced.state === "failed") {
        throw new ApiError(
          "sync_failed",
          synced.error ?? "SEC filings and verified numbers could not be synced.",
          502,
        );
      }

      let initialJob = synced;
      let autoResearch = false;
      let notice = synced.state === "partial"
        ? "Some SEC data could not be refreshed. Available verified results are shown."
        : "";
      if (bootstrap.analysis_configured) {
        setProgress("Starting filing analysis…");
        try {
          initialJob = await api<Job>("/api/jobs/analyze", {
            method: "POST",
            body: JSON.stringify({ ticker: resolvedTicker }),
          });
          autoResearch = true;
        } catch (reason) {
          notice = reason instanceof ApiError
            ? reason.message
            : "Verified numbers are ready, but filing analysis could not start.";
        }
      } else {
        notice = "Verified SEC numbers are ready. Qualitative analysis is temporarily unavailable.";
      }
      navigate(`/companies/${resolvedTicker}`, {
        state: { initialJob, autoResearch, notice },
      });
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Research could not be started.");
    } finally {
      setLoading(false);
      setProgress("");
    }
  }

  return <main className="page"><header className="research-hero"><p className="page-eyebrow">Before you buy</p><h1>Understand the business before the price.</h1><p>Get a concise company brief, downside lenses, transparent valuation assumptions, and exact SEC evidence.</p><form className="ticker-search" onSubmit={research}><label className="sr-only" htmlFor="research-ticker">Ticker symbol</label><input id="research-ticker" className="input mono-input" value={ticker} onChange={event => setTicker(event.target.value.toUpperCase())} placeholder="Enter a ticker, e.g. MSFT" required disabled={loading} /><button className="button primary" disabled={loading}>{loading ? progress || "Starting research…" : "Research company"}</button></form>{error && <div className="notice">{error}</div>}<p className="hero-note">No saved watch condition, payment, or brokerage connection required.</p></header>
    <JobProgress job={job} />
    <section className="section"><div className="surface-header"><div><span className="section-kicker">Recent research</span><h2>Companies you follow</h2></div><span className="surface-meta">Verified SEC data only</span></div>{resource.data?.companies.length ? <div className="research-list">{resource.data.companies.map(row => <Link className="research-row" to={`/companies/${row.ticker}${demoSuffix}`} key={row.ticker}><span><strong>{row.ticker}</strong><small>{row.compressed_verified_read ?? "Research brief is ready to assemble"}</small></span><span>Open brief →</span></Link>)}</div> : <div className="empty-state compact"><span aria-hidden="true">—</span><div><strong>No company research yet</strong><p>Use the ticker field above to begin.</p></div></div>}</section></main>;
}
