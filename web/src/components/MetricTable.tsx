import type { MetricRow, MetricState } from "../types";

const STATE_TEXT: Record<MetricState, string> = { computed: "Computed", unavailable: "Unavailable", not_applicable: "Not applicable", withheld: "Withheld" };
const STATE_CLASS: Record<MetricState, string> = { computed: "computed", unavailable: "", not_applicable: "", withheld: "withheld" };

export function MetricTable({ rows, summary }: { rows: MetricRow[]; summary?: string }) {
  return <div>{summary && <p className="metric-summary">{summary}</p>}<div className="table-scroll"><table className="metric-table">
    <caption className="sr-only">Deterministic SEC XBRL metric results — the same six starter metrics for every issuer</caption>
    <thead><tr><th>Metric</th><th>Value</th><th>Method & source</th><th>Status</th></tr></thead>
    <tbody>{rows.map(row => <tr key={row.source_computation_id}>
      <td>{row.metric}</td><td>{row.value}</td><td><code className="formula">{row.formula}</code><span className="metric-source">computation #{row.source_computation_id} · computed as of {row.effective_as_of}</span></td>
      <td><span className={`trust${STATE_CLASS[row.state] ? ` ${STATE_CLASS[row.state]}` : ""}`} title={row.state_label} aria-label={row.state_label}>{row.state === "computed" ? "✓ " : ""}{STATE_TEXT[row.state]}</span></td>
    </tr>)}</tbody>
  </table></div></div>;
}
