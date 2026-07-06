import type { Job } from "../types";

export function JobProgress({ job }: { job: Job | null }) {
  if (!job) return null;
  return <div className="job-list" aria-live="polite"><div className="mono muted">{job.kind}: {job.state}</div>{job.items.map(item => <div key={item.key} className={`job-item ${item.state}`}>{item.key} — {item.message}</div>)}{job.error && <div className="notice">{job.error}</div>}</div>;
}
