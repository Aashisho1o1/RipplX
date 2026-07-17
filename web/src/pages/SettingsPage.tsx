import { useState, type FormEvent } from "react";
import { api, ApiError } from "../api/client";
import { useBootstrap } from "../context/BootstrapContext";
import type { Bootstrap } from "../types";

export function SettingsPage() {
  const { bootstrap, refresh } = useBootstrap();
  const [identity, setIdentity] = useState(bootstrap.sec_user_agent);
  const [period, setPeriod] = useState(bootstrap.period);
  const [key, setKey] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const hosted = bootstrap.account_email !== null;

  async function save(event: FormEvent) {
    event.preventDefault(); setError(""); setMessage("");
    try {
      const payload: Record<string, unknown> = { period };
      if (!hosted) payload.sec_user_agent = identity;
      await api<Bootstrap>("/api/settings", { method: "PUT", body: JSON.stringify(payload) });
      if (key.trim()) {
        await api<void>("/api/settings/provider-key", {
          method: "PUT",
          body: JSON.stringify({ api_key: key }),
        });
      }
      setKey(""); setMessage("Settings saved."); refresh();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Settings could not be saved.");
    }
  }

  async function clearKey() {
    setError("");
    try {
      await api<void>("/api/settings/provider-key", { method: "DELETE" });
      setMessage("Provider API key cleared from this session."); refresh();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "The provider key could not be cleared.");
    }
  }

  async function logout() {
    setError("");
    try {
      await api<void>("/api/auth/logout", { method: "POST" });
      window.location.assign("/");
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Could not sign out. Try again.");
    }
  }

  return <main className="page narrow"><header className="page-header"><div><p className="page-eyebrow">Preferences</p><h1 className="page-title">Settings</h1><p className="page-subtitle">Manage your analysis connection and default brief window.</p></div></header><form className="settings-form" onSubmit={save}>{!hosted && <section className="surface settings-section"><div className="settings-heading"><span className="settings-index">01</span><div><h2>SEC identity</h2><p>EDGAR requires every automated reader to identify itself.</p></div></div><div className="field"><label htmlFor="user-agent">Contact email</label><input id="user-agent" className="input mono-input" type="email" required value={identity} onChange={event => setIdentity(event.target.value)} /><p className="helper">Used only in the request User-Agent sent to the SEC.</p></div></section>}<section className="surface settings-section"><div className="settings-heading"><span className="settings-index">{hosted ? "01" : "02"}</span><div><h2>Analysis connection</h2><p>Your filing reader and its session-only credential.</p></div><span className={`connection-status${bootstrap.analysis_configured ? " connected" : ""}`}><i />{bootstrap.analysis_configured ? "Ready" : "Setup needed"}</span></div><div className="field"><label htmlFor="analysis-model">Analysis model</label><input id="analysis-model" className="input mono-input" readOnly value={bootstrap.model} /><p className="helper">The model is configured by the server operator{bootstrap.provider ? ` through ${bootstrap.provider}` : ""}.</p></div><div className="field"><label htmlFor="api-key">{bootstrap.provider ?? "Provider"} API key</label><input id="api-key" className="input mono-input" type="password" autoComplete="off" placeholder={bootstrap.api_key_configured ? "Configured for this session" : "Session only — never persisted"} value={key} onChange={event => setKey(event.target.value)} /><p className="helper">The key travels over HTTPS through RipplX to the configured provider. It stays only in server memory, is isolated to this browser session, and is cleared on server restart.</p>{bootstrap.api_key_configured && <button type="button" className="button ghost" onClick={clearKey}>Clear session key</button>}</div></section><section className="surface settings-section"><div className="settings-heading"><span className="settings-index">{hosted ? "02" : "03"}</span><div><h2>Reading window</h2><p>Choose how much recent filing activity appears in your brief.</p></div></div><div className="field compact-field"><label htmlFor="period">Default period</label><select id="period" className="input" value={period} onChange={event => setPeriod(event.target.value)}>{["30d", "60d", "90d", "180d", "1y"].map(value => <option key={value}>{value}</option>)}</select></div>{hosted && <div className="field"><div className="divider-label">Signed in as {bootstrap.account_email}</div><button type="button" className="button" onClick={logout}>Sign out</button></div>}</section>{error && <div className="field-error">{error}</div>}{message && <div className="notice neutral">{message}</div>}<div className="settings-actions"><span>Changes apply only to your workspace.</span><button className="button primary button-large">Save settings</button></div></form></main>;
}
