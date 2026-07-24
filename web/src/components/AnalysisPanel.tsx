import { useState } from "react";
import type { FilingType } from "../types";
import { FilingTypePicker } from "./FilingTypePicker";

export function AnalysisPanel({
  configured,
  onAnalyze,
  onConfigure,
  onDemo,
  tickers,
}: {
  configured: boolean;
  onAnalyze: (formType: FilingType, ticker?: string) => void;
  onConfigure: () => void;
  onDemo?: () => void;
  tickers?: string[];
}) {
  const [formType, setFormType] = useState<FilingType>("latest");
  const [ticker, setTicker] = useState("");
  // Only offer a company choice when there is genuine ambiguity (more than one tracked
  // company). With one company, or on a company-scoped page, the target is unambiguous.
  const choosable = tickers && tickers.length > 1;

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

  const selectedForm = formType === "latest" ? "newest filing" : `newest ${formType}`;
  const target = ticker ? ` for ${ticker}` : choosable ? " across all tracked companies" : "";
  return <div className="analysis-panel">
    <div className="analysis-intro">
      <div><strong>Evidence-first analysis</strong><p>RipplX analyzes only the newest filing in the family you choose — it never falls back to older filings. Exact SEC quotations and deterministic checks gate every published finding.</p></div>
    </div>
    {choosable && <div className="field compact-field">
      <label htmlFor="analysis-company">Company</label>
      <select id="analysis-company" className="input" value={ticker} onChange={event => setTicker(event.target.value)}>
        <option value="">Newest across all tracked</option>
        {tickers.map(value => <option key={value} value={value}>{value}</option>)}
      </select>
    </div>}
    <FilingTypePicker value={formType} onChange={setFormType} />
    <div className="analysis-submit">
      <p>Ready to analyze the <strong>{selectedForm}</strong>{target}.</p>
      <button className="button primary button-large" onClick={() => onAnalyze(formType, ticker || undefined)}>Run analysis <span aria-hidden="true">→</span></button>
    </div>
  </div>;
}
