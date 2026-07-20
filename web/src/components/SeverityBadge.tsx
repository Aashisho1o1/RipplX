import type { Severity } from "../types";

export function SeverityBadge({ severity }: { severity: Severity }) {
  const tone = severity === "CRITICAL" ? "critical" : severity === "HIGH" || severity === "MEDIUM" ? "amber" : "neutral";
  return <span className={`pill ${tone}`} title="Model-assessed importance">AI-selected · {severity.toLowerCase()}</span>;
}
