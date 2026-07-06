import type { Posture } from "../types";

export function PosturePill({ posture }: { posture: Posture }) {
  const tone = posture === "critical_review" ? "critical" : posture === "risk_review" ? "amber" : posture === "insufficient_data" ? "neutral" : "teal";
  return <span className={`pill ${tone}`}>{posture}</span>;
}
