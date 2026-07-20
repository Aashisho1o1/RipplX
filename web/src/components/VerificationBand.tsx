import type { Verification, VerificationCheck } from "../types";

export const CHECK_LABEL: Record<string, string> = {
  V1: "Every number shown traces to SEC XBRL or an exact quotation",
  V2a: "Balance sheet ties out: assets = liabilities + equity",
  V2b: "Balance-sheet cash change ties to the cash-flow statement",
  V2c: "Revenue is at least gross profit, which is at least operating income",
  V2d: "Segment dimensions",
  V4: "Every quotation is verbatim at its declared position in the filing",
  V5: "Output schema, disclaimer, and no-advice hygiene",
};

const VERDICT_LABEL: Record<string, string> = {
  PASS: "Passed", FAIL: "Failed", WARN: "Warning", SKIPPED_NOT_APPLICABLE: "Not applicable",
};
const OVERALL: Record<Verification["verdict"], { label: string; tone: string }> = {
  PASS: { label: "All checks passed", tone: "verified" },
  PASS_WITH_WARNINGS: { label: "Passed with data-quality warnings", tone: "amber" },
  FAIL: { label: "A blocking check failed", tone: "critical" },
};
const GATE_CHECKS = new Set(["V1", "V4", "V5"]);
const DATA_QUALITY_CHECK = /^V2[a-z]?$/;

export function checkLabel(checkId: string): string { return CHECK_LABEL[checkId] ?? checkId; }
export function verdictLabel(verdict: string): string { return VERDICT_LABEL[verdict] ?? verdict.replaceAll("_", " ").toLowerCase(); }

function CheckGroup({ title, note, checks }: { title: string; note: string; checks: VerificationCheck[] }) {
  if (!checks.length) return null;
  return <div className="check-group"><h3 className="check-group-title">{title}<small>{note}</small></h3><div className="check-list">{checks.map((check, index) => <div className={`gate-check-row ${check.verdict.toLowerCase()}`} key={`${check.check_id}-${index}`}><code>{check.check_id}</code><span className="check-name">{checkLabel(check.check_id)}</span><span className="check-verdict">{verdictLabel(check.verdict)}</span>{check.detail && <small className="check-detail">{check.detail}</small>}</div>)}</div></div>;
}

export function VerificationBand({ verification }: { verification: Verification | null }) {
  if (!verification?.checks.length) return null;
  const gate = verification.checks.filter(check => GATE_CHECKS.has(check.check_id));
  const dataQuality = verification.checks.filter(check => DATA_QUALITY_CHECK.test(check.check_id));
  const other = verification.checks.filter(check => !GATE_CHECKS.has(check.check_id) && !DATA_QUALITY_CHECK.test(check.check_id));
  const overall = OVERALL[verification.verdict];
  return <section className="section verification-band" aria-labelledby="verification-heading"><header className="reading-heading"><div><p className="section-kicker">Deterministic publication gate</p><h2 id="verification-heading">What was checked</h2></div><span className={`pill ${overall.tone}`}>{overall.label}</span></header><p className="metric-caption">These checks prove provenance, exactness, and hygiene. They do not decide whether a change matters to you.</p><CheckGroup title="Publication gate" note="Blocking — a failure withholds the finding" checks={gate} /><CheckGroup title="Accounting data quality" note="Non-blocking — reported, never a gate" checks={dataQuality} /><CheckGroup title="Other recorded checks" note="Recorded for this attempt" checks={other} /></section>;
}
