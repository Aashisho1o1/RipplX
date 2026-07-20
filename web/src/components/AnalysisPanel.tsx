import { useState } from "react";
import type { FilingType } from "../types";
import { FilingTypePicker } from "./FilingTypePicker";

export function AnalysisPanel({
  configured,
  onAnalyze,
  onConfigure,
  onDemo,
}: {
  configured: boolean;
  onAnalyze: (formType: FilingType) => void;
  onConfigure: () => void;
  onDemo?: () => void;
}) {
  const [formType, setFormType] = useState<FilingType>("latest");

  if (!configured) {
    return <div className="analysis-empty">
      <div className="panel-icon" aria-hidden="true">R</div>
      <h3>Connect your analysis model</h3>
      <p>RipplX needs a model name and provider key to read filing text. Tracking, SEC syncing, and verified XBRL numbers work without one — and the bundled sample brief shows the full evidence path with no key at all.</p>
      <div className="actions stacked-mobile">
        <button className="button primary" onClick={onConfigure}>Configure analysis</button>
        {onDemo && <button className="button" onClick={onDemo}>Open the sample brief</button>}
      </div>
    </div>;
  }

  const selectedLabel = formType === "latest" ? "newest filing" : `newest ${formType}`;
  return <div className="analysis-panel">
    <div className="analysis-intro">
      <div><strong>Evidence-first analysis</strong><p>RipplX analyzes only the newest filing in the family you choose — it never falls back to older filings. Exact SEC quotations and deterministic checks gate every published finding.</p></div>
    </div>
    <FilingTypePicker value={formType} onChange={setFormType} />
    <div className="analysis-submit">
      <p>Ready to analyze the <strong>{selectedLabel}</strong>.</p>
      <button className="button primary button-large" onClick={() => onAnalyze(formType)}>Run analysis <span aria-hidden="true">→</span></button>
    </div>
  </div>;
}
