import type { ResearchRun } from "../types";

interface Props {
  run: ResearchRun | null;
  canRun: boolean;
  demo: boolean;
  onRun: () => void;
}

export function DeepResearchPanel({ run, canRun, demo, onRun }: Props) {
  const report = run?.report;
  const observations = new Map(
    report?.observations.map((row) => [row.observation_id, row]) ?? [],
  );
  const busy = run?.status === "queued" || run?.status === "running";

  return (
    <section className="section">
      <div className="surface-header">
        <div>
          <span className="section-kicker">02 · Connected research</span>
          <h2>Change → driver → conditional implication</h2>
        </div>
        {!demo && (
          <button className="button primary" disabled={!canRun || busy} onClick={onRun}>
            {busy ? "Connecting evidence…" : report ? "Refresh research" : "Connect the research"}
          </button>
        )}
      </div>

      {!report && (
        <div className="empty-state compact">
          <span aria-hidden="true">↗</span>
          <div>
            <strong>{run?.status === "failed" ? "Deep research did not complete" : "Verified pieces are ready to connect"}</strong>
            <p>
              {demo
                ? "The static demo keeps this optional model pass off; the verified brief remains available."
                : canRun
                  ? "RipplX can connect filing evidence, financial quality, valuation, and peers in one bounded pass."
                : "Analyze a filing and configure the research model to create this report."}
            </p>
          </div>
        </div>
      )}

      {report && (
        <>
          <p className="research-summary">{report.summary}</p>
          <div className="obligation-strip" aria-label="Research coverage">
            {report.obligations.map((row) => (
              <span className={row.state} key={row.obligation}>
                {row.obligation.replaceAll("_", " ")} · {row.state}
              </span>
            ))}
          </div>
          {report.insights.length ? (
            <div className="research-insight-list">
              {report.insights.map((insight) => (
                <article key={insight.insight_id}>
                  <div className="research-insight-heading">
                    <span className={`impact-effect ${insight.scenario}`}>{insight.scenario}</span>
                    <span>{insight.evidence_status.replaceAll("_", " ")}</span>
                  </div>
                  <h3>{insight.headline}</h3>
                  <p>{insight.evidence_summary}</p>
                  <div className="research-chain">
                    <div><small>Driver</small><strong>{insight.driver}</strong></div>
                    <span aria-hidden="true">→</span>
                    <div><small>Mechanism</small><strong>{insight.mechanism.replaceAll("_", " ")}</strong></div>
                    <span aria-hidden="true">→</span>
                    <div><small>Possible implication</small><strong>{insight.implication}</strong></div>
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
          ) : (
            <div className="notice neutral">No qualitative insight passed verification. Deterministic metrics remain available.</div>
          )}
          <details className="research-method">
            <summary>Evidence gaps & method</summary>
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
