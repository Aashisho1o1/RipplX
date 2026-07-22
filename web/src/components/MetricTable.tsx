import { Fragment, useState } from "react";
import type { MetricRow, MetricState } from "../types";

const STATE_TEXT: Record<MetricState, string> = { computed: "Computed", unavailable: "Unavailable", not_applicable: "Not applicable", withheld: "Withheld" };
const STATE_CLASS: Record<MetricState, string> = { computed: "computed", unavailable: "", not_applicable: "", withheld: "withheld" };

export function MetricTable({ rows, summary }: { rows: MetricRow[]; summary?: string }) {
  const [openId, setOpenId] = useState<number | null>(null);
  return <div>{summary && <p className="metric-summary">{summary}</p>}<div className="table-scroll"><table className="metric-table">
    <caption className="sr-only">Deterministic SEC XBRL metric results — the same six starter metrics for every issuer</caption>
    <thead><tr><th>Metric</th><th>Value</th><th>Method & source</th><th>Status</th></tr></thead>
    <tbody>{rows.map(row => {
      const open = openId === row.source_computation_id;
      const derivationId = `metric-derivation-${row.source_computation_id}`;
      return <Fragment key={row.source_computation_id}>
        <tr>
          <td>{row.metric}</td>
          <td>{row.derivation ? <button
            className="metric-value-toggle"
            type="button"
            aria-expanded={open}
            aria-controls={derivationId}
            aria-label={`${open ? "Hide" : "Show"} derivation for ${row.metric}: ${row.value}`}
            onClick={() => setOpenId(open ? null : row.source_computation_id)}
          >{row.value}<span className="metric-chevron" data-open={open} aria-hidden="true">⌄</span></button> : row.value}</td>
          <td><code className="formula">{row.formula}</code><span className="metric-source">computation #{row.source_computation_id} · computed as of {row.effective_as_of}</span>{row.state !== "computed" && <span className="metric-state-detail">Why: {row.state_label}</span>}</td>
          <td><span className={`trust${STATE_CLASS[row.state] ? ` ${STATE_CLASS[row.state]}` : ""}`} title={row.state_label} aria-label={row.state_label}>{row.state === "computed" ? "✓ " : ""}{STATE_TEXT[row.state]}</span></td>
        </tr>
        {open && row.derivation && <tr className="metric-derivation-row" id={derivationId}>
          <td colSpan={4}><div className="metric-derivation">
            <div className="derivation-head"><div><p className="section-kicker">SEC XBRL derivation</p><h3>How this number was built</h3></div><code className="formula">{row.derivation.formula_version}</code></div>
            <p className="derivation-expression">{row.derivation.expression}</p>
            {row.derivation.inputs.length > 0 ? <div className="table-scroll"><table className="derivation-table">
              <thead><tr><th>Concept</th><th>Value</th><th>Unit</th><th>Period</th><th>Accession</th></tr></thead>
              <tbody>{row.derivation.inputs.map((input, index) => <tr key={`${input.accession}-${input.concept}-${index}`}>
                <td><code>{input.taxonomy}:{input.concept}</code></td><td>{input.value}</td><td><code>{input.unit}</code></td><td>{input.period}</td><td><code>{input.accession}</code></td>
              </tr>)}</tbody>
            </table></div> : <p className="derivation-empty">No SEC XBRL fact met the point-in-time test, so no value is shown.</p>}
          </div></td>
        </tr>}
      </Fragment>;
    })}</tbody>
  </table></div></div>;
}
