import { useCallback, useState, type FormEvent } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { api, ApiError, storeAuthToken } from "./api/client";
import { AppShell } from "./components/AppShell";
import { BootstrapContext } from "./context/BootstrapContext";
import { useResource } from "./hooks/useResource";
import { BriefPage } from "./pages/BriefPage";
import { CompanyPage } from "./pages/CompanyPage";
import { FilingPage } from "./pages/FilingPage";
import { HoldingsPage } from "./pages/HoldingsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SetupPage } from "./pages/SetupPage";
import type { Bootstrap } from "./types";

function RoutedApp() {
  const location = useLocation();
  const [authAttempted, setAuthAttempted] = useState(false);
  const load = useCallback((signal: AbortSignal) => api<Bootstrap>("/api/bootstrap", { signal }), []); const resource = useResource(load, []);
  if (!resource.data && resource.error instanceof ApiError && resource.error.code === "authentication_required") {
    return <UnlockScreen loading={resource.loading} rejected={authAttempted} onUnlock={token => { storeAuthToken(token); setAuthAttempted(true); resource.refresh(); }} />;
  }
  if (!resource.data) return <main className="setup"><p className={resource.loading ? "loading" : "notice"}>{resource.loading ? "Starting RipplX…" : resource.error?.message ?? "RipplX could not start."}</p></main>;
  const demoPreview = resource.data.setup_required && new URLSearchParams(location.search).get("demo") === "1";
  return <BootstrapContext.Provider value={{ bootstrap: resource.data, refresh: resource.refresh }}><Routes>{resource.data.setup_required && !demoPreview && <Route path="*" element={<SetupPage onComplete={resource.refresh} />} />} {(!resource.data.setup_required || demoPreview) && <Route element={<AppShell />}><Route path="/brief" element={<BriefPage />} /><Route path="/holdings" element={<HoldingsPage />} /><Route path="/settings" element={<SettingsPage />} /><Route path="/filings/:accession" element={<FilingPage />} /><Route path="/companies/:ticker" element={<CompanyPage />} /><Route path="*" element={<Navigate to="/brief" replace />} /></Route>}</Routes></BootstrapContext.Provider>;
}

function UnlockScreen({ loading, rejected, onUnlock }: { loading: boolean; rejected: boolean; onUnlock: (token: string) => void }) {
  const [token, setToken] = useState("");
  function submit(event: FormEvent) {
    event.preventDefault();
    onUnlock(token);
  }
  return <main className="setup"><div className="watermark" aria-hidden="true" /><section className="setup-card unlock-card"><div className="setup-kicker">RipplX hosted alpha</div><h1>Unlock this session</h1><p className="setup-lede">Enter the operator access token. It is kept only in page memory and is cleared on refresh.</p><form className="form-grid" onSubmit={submit}><div className="field"><label htmlFor="access-token">Access token</label><input id="access-token" className="input mono-input" type="password" autoComplete="current-password" required value={token} onChange={event => setToken(event.target.value)} /></div>{rejected && !loading && <div className="field-error">That access token was not accepted.</div>}<button className="button primary" disabled={loading || !token.trim()}>{loading ? "Unlocking…" : "Unlock"}</button></form></section></main>;
}

export default function App() { return <BrowserRouter><RoutedApp /></BrowserRouter>; }
