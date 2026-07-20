import { useCallback, useState, type FormEvent } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { api, ApiError } from "./api/client";
import { AppShell } from "./components/AppShell";
import { BootstrapContext } from "./context/BootstrapContext";
import { useResource } from "./hooks/useResource";
import { BriefPage } from "./pages/BriefPage";
import { CompanyPage } from "./pages/CompanyPage";
import { FilingPage } from "./pages/FilingPage";
import { CompaniesPage } from "./pages/CompaniesPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SetupPage } from "./pages/SetupPage";
import type { AuthChallenge, Bootstrap } from "./types";

function RoutedApp() {
  const location = useLocation();
  const load = useCallback(
    (signal: AbortSignal) => api<Bootstrap>("/api/bootstrap", { signal }),
    [],
  );
  const resource = useResource(load, []);
  if (!resource.data && resource.error instanceof ApiError && resource.error.code === "authentication_required") {
    return <SignInScreen onSignedIn={resource.refresh} />;
  }
  if (!resource.data) {
    return <main className="setup"><p className={resource.loading ? "loading" : "notice"}>{resource.loading ? "Starting RipplX…" : resource.error?.message ?? "RipplX could not start."}</p></main>;
  }
  const demoPreview = resource.data.setup_required && new URLSearchParams(location.search).get("demo") === "1";
  return <BootstrapContext.Provider value={{ bootstrap: resource.data, refresh: resource.refresh }}><Routes>{resource.data.setup_required && !demoPreview && <Route path="*" element={<SetupPage onComplete={resource.refresh} />} />} {(!resource.data.setup_required || demoPreview) && <Route element={<AppShell />}><Route path="/brief" element={<BriefPage />} /><Route path="/companies" element={<CompaniesPage />} /><Route path="/settings" element={<SettingsPage />} /><Route path="/filings/:accession" element={<FilingPage />} /><Route path="/companies/:ticker" element={<CompanyPage />} /><Route path="*" element={<Navigate to="/brief" replace />} /></Route>}</Routes></BootstrapContext.Provider>;
}

function SignInScreen({ onSignedIn }: { onSignedIn: () => void }) {
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [challenge, setChallenge] = useState<AuthChallenge | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function requestCode(event?: FormEvent) {
    event?.preventDefault();
    setLoading(true); setError("");
    try {
      const created = await api<AuthChallenge>("/api/auth/request-code", {
        method: "POST",
        body: JSON.stringify({ email }),
      });
      setChallenge(created); setCode("");
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "The sign-in code could not be sent.");
    } finally { setLoading(false); }
  }

  async function verifyCode(event: FormEvent) {
    event.preventDefault();
    if (!challenge) return;
    setLoading(true); setError("");
    try {
      await api<void>("/api/auth/verify-code", {
        method: "POST",
        body: JSON.stringify({ challenge_id: challenge.challenge_id, code }),
      });
      onSignedIn();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "That sign-in code was not accepted.");
    } finally { setLoading(false); }
  }

  return <main className="setup"><section className="setup-card unlock-card"><div className="setup-kicker">RipplX public alpha</div><h1>{challenge ? "Check your email" : "Sign in to RipplX"}</h1><p className="setup-lede">{challenge ? <>Enter the six-digit code sent to <strong>{email.trim()}</strong>. It expires in 10 minutes.</> : "Enter your email and we’ll send a simple sign-in code. No password or invitation needed."}</p>{challenge ? <form className="form-grid" onSubmit={verifyCode}><div className="field"><label htmlFor="email-code">Sign-in code</label><input id="email-code" className="input mono-input otp-input" type="text" inputMode="numeric" autoComplete="one-time-code" required minLength={6} maxLength={6} value={code} onChange={event => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))} /></div>{error && <div className="field-error">{error}</div>}<button className="button primary" disabled={loading || code.length !== 6}>{loading ? "Signing in…" : "Sign in"}</button><div className="auth-secondary"><button type="button" className="button ghost" disabled={loading} onClick={() => requestCode()}>Send another code</button><button type="button" className="button ghost" disabled={loading} onClick={() => { setChallenge(null); setCode(""); setError(""); }}>Use a different email</button></div></form> : <form className="form-grid" onSubmit={requestCode}><div className="field"><label htmlFor="sign-in-email">Email</label><input id="sign-in-email" className="input" type="email" autoComplete="email" required value={email} onChange={event => setEmail(event.target.value)} placeholder="you@example.com" /></div>{error && <div className="field-error">{error}</div>}<button className="button primary" disabled={loading || !email.trim()}>{loading ? "Sending code…" : "Email me a code"}</button></form>}</section></main>;
}

export default function App() { return <BrowserRouter><RoutedApp /></BrowserRouter>; }
