export function ShadowToggle({ value, onChange, disabled = false }: { value: boolean; onChange: () => void; disabled?: boolean }) {
  return <button type="button" className={`toggle${value ? " on" : ""}`} aria-pressed={value} onClick={onChange} disabled={disabled}><span className="toggle-knob" aria-hidden="true" />Show shadow signals (unvalidated, educational)</button>;
}
