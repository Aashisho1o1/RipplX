import type { Job } from "../types";

const JOB_STATE_LABEL: Record<Job["state"], string> = { queued: "Queued", running: "Running…", completed: "Completed", partial: "Finished — some items did not complete", failed: "Failed" };

export function JobProgress({ job }: { job: Job | null }) {
  if (!job) return null;
  const active = ["queued", "running"].includes(job.state);
  return <div className="job-list" aria-live="polite"><div className="job-heading"><span className={`job-spinner${active ? " active" : ""}`} aria-hidden="true" /><div><strong>{job.kind === "analysis" ? "Filing analysis" : "SEC sync"}</strong><small>{JOB_STATE_LABEL[job.state]}</small></div></div>{job.items.map(item => <div key={item.key} className={`job-item ${item.state}`}><span>{item.stage ?? item.key}</span><strong>{item.message || item.state}</strong></div>)}{job.error && <div className="notice">{job.error}</div>}</div>;
}
