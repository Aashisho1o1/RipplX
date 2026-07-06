import type { MetricRow } from "../types";

export function MetricTable({ rows }: { rows: MetricRow[] }) {
  return <div className="table-scroll"><table className="metric-table">
    <thead><tr><th>Metric</th><th>Value</th><th>Formula</th><th>✓</th></tr></thead>
    <tbody>{rows.map(row => <tr key={row.formula}>
      <td>{row.metric}</td><td>{row.value}</td><td><code className="formula">{row.formula}</code></td>
      <td><span className={`trust ${row.state === "computed" ? "computed" : "missing"}`} title={row.state_label} aria-label={row.state_label}>{row.state === "computed" ? "✓" : "—"}</span></td>
    </tr>)}</tbody>
  </table></div>;
}
