import type { MetricRow } from "../types";

export function MetricTable({ rows, showComputedMark = true }: { rows: MetricRow[]; showComputedMark?: boolean }) {
  return <div className="table-scroll"><table className="metric-table">
    <caption className="sr-only">Deterministic SEC XBRL metric results</caption>
    <thead><tr><th>Metric</th><th>Value</th><th>Method & source</th><th>Status</th></tr></thead>
    <tbody>{rows.map(row => <tr key={row.source_computation_id}>
      <td>{row.metric}</td><td>{row.value}</td><td><code className="formula">{row.formula}</code><span className="metric-source">computation #{row.source_computation_id} · computed as of {row.effective_as_of}</span></td>
      <td><span className={`trust ${row.state === "computed" ? "computed" : "missing"}`} title={row.state_label} aria-label={row.state_label}>{row.state === "computed" ? `${showComputedMark ? "✓ " : ""}Computed` : "—"}</span></td>
    </tr>)}</tbody>
  </table></div>;
}
