import { useState, type FormEvent } from "react";
import { api, ApiError } from "../api/client";
import { useBootstrap } from "../context/BootstrapContext";
import type { Bootstrap } from "../types";

export function SettingsPage() {
  const { bootstrap, refresh } = useBootstrap();
  const [identity, setIdentity] = useState(bootstrap.sec_user_agent);
  const [period, setPeriod] = useState(bootstrap.period);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [billing, setBilling] = useState(false);
  const hosted = bootstrap.account_email !== null;

  async function save(event: FormEvent) {
    event.preventDefault(); setError(""); setMessage("");
    try {
      const payload: Record<string, unknown> = { period };
      if (!hosted) payload.sec_user_agent = identity;
      await api<Bootstrap>("/api/settings", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      setMessage("Settings saved."); refresh();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Settings could not be saved.");
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

  async function openBilling(path: "checkout" | "portal") {
    setBilling(true); setError("");
    try {
      const result = await api<{ url: string }>(`/api/billing/${path}`, { method: "POST" });
      window.location.assign(result.url);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Billing could not be opened.");
      setBilling(false);
    }
  }

  return <main className="page narrow">
    <header className="page-header"><div><p className="page-eyebrow">Preferences</p><h1 className="page-title">Settings</h1><p className="page-subtitle">Manage your default brief window and account.</p></div></header>
    <form className="settings-form" onSubmit={save}>
      {!hosted && <section className="surface settings-section"><div className="settings-heading"><span className="settings-index">01</span><div><h2>SEC identity</h2><p>EDGAR requires every automated reader to identify itself.</p></div></div><div className="field"><label htmlFor="user-agent">Contact email</label><input id="user-agent" className="input mono-input" type="email" required value={identity} onChange={event => setIdentity(event.target.value)} /><p className="helper">Used only in the request User-Agent sent to the SEC.</p></div></section>}

      <section className="surface settings-section"><div className="settings-heading"><span className="settings-index">{hosted ? "01" : "02"}</span><div><h2>Analysis</h2><p>Included with RipplX—no provider account or API key required.</p></div></div><div className="field"><label htmlFor="analysis-model">Analysis model</label><input id="analysis-model" className="input mono-input" readOnly value={bootstrap.model || "Managed by RipplX"} /><p className="helper">{bootstrap.analysis_configured ? `Ready${bootstrap.provider ? ` through ${bootstrap.provider}` : ""}. RipplX provides the model connection.` : "Temporarily unavailable because the server connection is not configured."}</p></div></section>

      {hosted && <section className="surface settings-section"><div className="settings-heading"><span className="settings-index">02</span><div><h2>Subscription</h2><p>Founding plan status: {bootstrap.billing_status.replaceAll("_", " ")}.</p></div></div>{bootstrap.billing_configured ? <button type="button" className="button" disabled={billing} onClick={() => openBilling(bootstrap.billing_status === "free" ? "checkout" : "portal")}>{billing ? "Opening…" : bootstrap.billing_status === "free" ? "Start founding plan" : "Manage subscription"}</button> : <p className="helper">Billing is not configured by the server operator.</p>}</section>}

      <section className="surface settings-section"><div className="settings-heading"><span className="settings-index">03</span><div><h2>Reading window</h2><p>Choose how much recent filing activity appears in your brief.</p></div></div><div className="field compact-field"><label htmlFor="period">Default period</label><select id="period" className="input" value={period} onChange={event => setPeriod(event.target.value)}>{["30d", "60d", "90d", "180d", "1y"].map(value => <option key={value}>{value}</option>)}</select></div>{hosted && <div className="field"><div className="divider-label">Signed in as {bootstrap.account_email}</div><button type="button" className="button" onClick={logout}>Sign out</button></div>}</section>

      {error && <div className="field-error">{error}</div>}
      {message && <div className="notice neutral">{message}</div>}
      <div className="settings-actions"><span>Changes apply only to your workspace.</span><button className="button primary button-large">Save settings</button></div>
    </form>
  </main>;
}
