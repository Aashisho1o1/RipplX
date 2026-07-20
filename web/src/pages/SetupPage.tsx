import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { Bootstrap } from "../types";

const disclaimer = "Educational analysis of public information for the portfolio owner's own decision-making. Not individualized investment advice. Data may be incomplete or delayed.";

export function SetupPage({ onComplete }: { onComplete: () => void }) {
  const [identity, setIdentity] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const navigate = useNavigate();
  async function submit(event: FormEvent) {
    event.preventDefault(); setSaving(true); setError("");
    try { await api<Bootstrap>("/api/settings", { method: "PUT", body: JSON.stringify({ sec_user_agent: identity }) }); onComplete(); navigate("/brief"); }
    catch (reason) { setError(reason instanceof ApiError ? reason.message : "Could not save the SEC identity."); }
    finally { setSaving(false); }
  }
  return <main className="setup"><section className="setup-card"><div className="setup-kicker">RipplX</div><h1>“I own 12 stocks. I do not read every 8-K, 10-Q, and 10-K. I want to know when something actually important changed.”</h1><p className="setup-lede">Open-source filing intelligence — watches your tickers, reads new SEC filings, highlights material changes, checks every number deterministically, and shows why it matters, with citations.</p><form onSubmit={submit} className="form-grid"><div className="field"><label htmlFor="identity">SEC User-Agent identity</label><input id="identity" className="input mono-input" type="email" required placeholder="you@example.com" value={identity} onChange={event => setIdentity(event.target.value)} />{error && <div className="field-error">{error}</div>}<p className="helper">EDGAR asks every reader to identify themselves; RipplX throttles to ≤8 requests/sec. No account, no API key needed to start.</p></div><div className="actions"><button className="button primary" disabled={saving}>{saving ? "Saving…" : "Continue"}</button><button type="button" className="button secondary" onClick={() => navigate("/brief?demo=1")}>See the demo brief</button></div></form></section><footer className="footer">{disclaimer}</footer></main>;
}
