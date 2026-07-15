import type { FilingType } from "../types";

const options: { value: FilingType; label: string; eyebrow: string; description: string }[] = [
  { value: "latest", label: "Latest filing", eyebrow: "Auto", description: "Analyze the newest 10-K, 10-Q, or 8-K available." },
  { value: "10-Q", label: "Quarterly report", eyebrow: "10-Q", description: "Revenue, margins, cash flow, risks, and quarterly updates." },
  { value: "10-K", label: "Annual report", eyebrow: "10-K", description: "Full-year performance, strategy, risks, and audited financials." },
  { value: "8-K", label: "Current event", eyebrow: "8-K", description: "Material events such as leadership, deals, earnings, or disclosures." },
];

export function FilingTypePicker({ value, onChange }: { value: FilingType; onChange: (value: FilingType) => void }) {
  return <fieldset className="filing-picker">
    <legend>Choose a filing type</legend>
    <p className="helper picker-helper">RipplX analyzes the newest eligible filing in the category you choose, including amendments.</p>
    <div className="filing-options">
      {options.map(option => <label className={`filing-option${value === option.value ? " selected" : ""}`} key={option.value}>
        <input type="radio" name="filing-type" value={option.value} checked={value === option.value} onChange={() => onChange(option.value)} />
        <span className="filing-option-code">{option.eyebrow}</span>
        <span className="filing-option-copy"><strong>{option.label}</strong><small>{option.description}</small></span>
        <span className="filing-option-check" aria-hidden="true">{value === option.value ? "✓" : ""}</span>
      </label>)}
    </div>
  </fieldset>;
}
