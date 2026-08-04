import type { ChangeImpact, ResearchRun } from "../types";

interface Props {
  run: ResearchRun | null;
  canRun: boolean;
  demo: boolean;
  fallbackChanges: ChangeImpact[];
  onRun: () => void;
}

export function DeepResearchPanel({ run, canRun, demo, fallbackChanges, onRun }: Props) {
  const report = run?.report;
  const observations = new Map(
    report?.observations.map((row) => [row.observation_id, row]) ?? [],
  );
  const busy = run?.status === "queued" || run?.status === "running";
  const fallback = !report?.insights.length && fallbackChanges.length > 0;
  const collected = report?.insights.length === 0
    ? [...report.observations]
      .filter((row) => row.evidence_label !== "unavailable")
      .sort((left, right) => {
        const priority = ["get_verified_changes", "search_filing_sections", "get_financial_context", "get_peer_context"];
        return priority.indexOf(left.tool) - priority.indexOf(right.tool);
      })
      .slice(0, 3)
    : [];

  return (
    <section className="section">
      <div className="surface-header">
        <div>
          <span className="section-kicker">02 · Filing changes</span>
          <h2>Verified changes and possible effects</h2>
        </div>
        {!demo && (
          <button className="button primary" disabled={!canRun || busy} onClick={onRun}>
            {busy ? "Reviewing evidence…" : run?.status === "failed" ? "Try deeper analysis again" : report ? "Run analysis again" : "Run deeper analysis"}
          </button>
        )}
      </div>

      {!report && !fallback && (
        <div className="empty-state compact">
          <span aria-hidden="true">↗</span>
          <div>
            <strong>No deeper interpretation yet</strong>
            <p>
              {demo
                ? "The showcase displays verified filing changes without running another model pass."
                : canRun
                  ? "Optional analysis can compare verified filing changes with financial context. Any possible effect remains conditional."
                  : "Analyze a filing and configure the research model to add a cautious interpretation."}
            </p>
          </div>
        </div>
      )}

      {fallback && (
        <>
          <p className="research-summary"><strong>{fallbackChanges.length} AI-selected change{fallbackChanges.length === 1 ? "" : "s"}</strong> · exact source quotations verified. No cause or stock effect is inferred here.</p>
          <div className="research-insight-list">
            {fallbackChanges.map((change) => (
              <article key={change.finding_id}>
                <div className="research-insight-heading">
                  <span>AI-selected change</span>
                  <span>Source quote verified</span>
                </div>
                <h3>{change.headline}</h3>
                <details>
                  <summary>Exact SEC evidence</summary>
                  {change.evidence.map((row) => <blockquote key={row.reference_id}>{row.quote}</blockquote>)}
                </details>
              </article>
            ))}
          </div>
          {run?.status === "failed" && <p className="metric-caption">Optional deeper analysis did not finish. No causal or stock-effect claim is shown.</p>}
        </>
      )}

      {report && report.insights.length === 0 && !fallback && (
        collected.length > 0 ? <>
          <p className="research-summary"><strong>Verified sources collected</strong> · RipplX withheld interpretation it could not support.</p>
          <div className="research-insight-list">
            {collected.map((observation) => <article key={observation.observation_id}>
              <div className="research-insight-heading"><span>{observation.evidence_label === "calculation" ? "Verified calculation" : "SEC fact"}</span><span>{observation.tool.replace("get_", "").replaceAll("_", " ")}</span></div>
              <p>{observation.text}</p>
              {observation.evidence.some(row => row.quote) && <details><summary>Exact evidence</summary>{observation.evidence.map(row => row.quote ? <blockquote key={row.reference_id}>{row.quote}</blockquote> : null)}</details>}
            </article>)}
          </div>
          {run.trace && Object.keys(run.trace.dropped_insights).length > 0 && <p className="metric-caption">{Object.keys(run.trace.dropped_insights).length} draft interpretation{Object.keys(run.trace.dropped_insights).length === 1 ? " was" : "s were"} withheld because the evidence standard was not met.</p>}
        </> : <div className="empty-state compact"><span aria-hidden="true">—</span><div><strong>No material qualitative change cleared verification</strong><p>This is not a clean bill of health. Use the verified financial trends and downside checks below.</p></div></div>
      )}

      {report && report.insights.length > 0 && (
        <>
          <p className="research-summary">{report.summary}</p>
          <p className="metric-caption">Source evidence is verified. The possible driver and effect below are model-generated, conditional, and not predictions.</p>
          <div className="research-insight-list">
            {report.insights.map((insight) => (
              <article key={insight.insight_id}>
                <div className="research-insight-heading">
                  <span>{insight.category.replaceAll("_", " ")}</span>
                  <span>Conditional interpretation</span>
                </div>
                <h3>{insight.headline}</h3>
                <p><strong>Observed evidence:</strong> {insight.evidence_summary}</p>
                <div className="research-chain">
                  <div><small>Possible driver</small><strong>{insight.driver}</strong></div>
                  <span aria-hidden="true">→</span>
                  <div><small>May affect</small><strong>{insight.mechanism.replaceAll("_", " ")}</strong></div>
                  <span aria-hidden="true">→</span>
                  <div><small>Possible effect</small><strong>{insight.implication}</strong></div>
                </div>
                <details>
                  <summary>Evidence, assumptions & limitations</summary>
                  {insight.observation_ids.map((id) => {
                    const observation = observations.get(id);
                    return observation ? (
                      <div className="research-evidence" key={id}>
                        <span>{observation.evidence_label}</span>
                        <p>{observation.text}</p>
                        {observation.evidence.map((evidence) =>
                          evidence.quote ? <blockquote key={evidence.reference_id}>{evidence.quote}</blockquote> : null,
                        )}
                      </div>
                    ) : null;
                  })}
                  <p><strong>Assumes:</strong> {insight.assumptions.join(" ")}</p>
                  <p><strong>Limits:</strong> {insight.limitations.join(" ")}</p>
                </details>
              </article>
            ))}
          </div>
          <details className="research-method">
            <summary>Coverage, evidence gaps & method</summary>
            <div className="obligation-strip" aria-label="Research coverage">
              {report.obligations.map((row) => (
                <span className={row.state} key={row.obligation}>
                  {row.obligation.replaceAll("_", " ")} · {row.state}
                </span>
              ))}
            </div>
            <p>Data cutoff: {report.data_cutoff}</p>
            {report.evidence_gaps.map((gap) => <p key={gap}>{gap}</p>)}
            {run.trace && (
              <p>
                {run.trace.tool_budget_used} research tools · {run.trace.turn_budget_used} model turns · {run.trace.repair_used ? "one repair used" : "no repair used"} · {run.trace.terminal_reason.replaceAll("_", " ")}
              </p>
            )}
          </details>
        </>
      )}
    </section>
  );
}
