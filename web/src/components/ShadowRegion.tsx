import type { ShadowSignal } from "../types";
import { PosturePill } from "./PosturePill";

export function ShadowRegion({ signals }: { signals: ShadowSignal[] }) {
  if (!signals.length) return null;
  return <section className="shadow-region" aria-label="Unvalidated shadow signals">
    <blockquote className="shadow-banner">⚠ Unvalidated shadow output — educational only, not a trade instruction. These hypothetical signals are logged to build an auditable track record; they are off by default and shown only with --signals.</blockquote>
    {signals.map(signal => <article className="shadow-card" key={signal.ticker}>
      <div className="filing-heading"><strong>{signal.ticker} — hypothetical signal: <span className="pill critical">{signal.signal}</span></strong><PosturePill posture={signal.posture} /></div>
      <dl><dt>Rules fired</dt><dd className="mono">{signal.rules_fired.join(", ")}</dd><dt>Rationale</dt><dd>{signal.rationale_withheld ? "⚠ rationale withheld — automated verification failed (manual review required)." : signal.rationale}</dd>{signal.counter_evidence && <><dt>Counter-evidence</dt><dd>{signal.counter_evidence}</dd></>}<dt>What would change this</dt><dd>{signal.what_would_change_this.join("; ")}</dd></dl>
    </article>)}
  </section>;
}
