import { useCallback } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { api } from "./api/client";
import { AppShell } from "./components/AppShell";
import { BootstrapContext } from "./context/BootstrapContext";
import { useResource } from "./hooks/useResource";
import { BriefPage } from "./pages/BriefPage";
import { CompanyPage } from "./pages/CompanyPage";
import { FilingPage } from "./pages/FilingPage";
import { HoldingsPage } from "./pages/HoldingsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SetupPage } from "./pages/SetupPage";
import { TrackRecordPage } from "./pages/TrackRecordPage";
import type { Bootstrap } from "./types";

function RoutedApp() {
  const location = useLocation();
  const load = useCallback((signal: AbortSignal) => api<Bootstrap>("/api/bootstrap", { signal }), []); const resource = useResource(load, []);
  if (!resource.data) return <main className="setup"><p className={resource.loading ? "loading" : "notice"}>{resource.loading ? "Starting RipplX…" : resource.error?.message ?? "RipplX could not start."}</p></main>;
  const demoPreview = resource.data.setup_required && new URLSearchParams(location.search).get("demo") === "1";
  return <BootstrapContext.Provider value={{ bootstrap: resource.data, refresh: resource.refresh }}><Routes>{resource.data.setup_required && !demoPreview && <Route path="*" element={<SetupPage onComplete={resource.refresh} />} />} {(!resource.data.setup_required || demoPreview) && <Route element={<AppShell />}><Route path="/brief" element={<BriefPage />} /><Route path="/holdings" element={<HoldingsPage />} /><Route path="/track-record" element={<TrackRecordPage />} /><Route path="/settings" element={<SettingsPage />} /><Route path="/filings/:accession" element={<FilingPage />} /><Route path="/companies/:ticker" element={<CompanyPage />} /><Route path="*" element={<Navigate to="/brief" replace />} /></Route>}</Routes></BootstrapContext.Provider>;
}

export default function App() { return <BrowserRouter><RoutedApp /></BrowserRouter>; }
