import { useCallback, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { DisclaimerFooter } from "../components/DisclaimerFooter";
import { MetricTable } from "../components/MetricTable";
import { OwnedWatchTag } from "../components/OwnedWatchTag";
import { PosturePill } from "../components/PosturePill";
import { RedFlagRow } from "../components/RedFlagRow";
import { SeverityBadge } from "../components/SeverityBadge";
import { ShadowRegion } from "../components/ShadowRegion";
import { useBootstrap } from "../context/BootstrapContext";
import { useResource } from "../hooks/useResource";
import type { FilingDetail, Verification } from "../types";

export function FilingPage() {
  const { accession = "" } = useParams(); const location = useLocation(); const navigate = useNavigate(); const { bootstrap } = useBootstrap();
  const demo = new URLSearchParams(location.search).get("demo") === "1";
  const load = useCallback((signal: AbortSignal) => api<FilingDetail>(`/api/filings/${accession}?demo=${demo}&include_signals=${bootstrap.signals}`, { signal }), [accession, demo, bootstrap.signals]);
  const resource = useResource(load, [accession, demo, bootstrap.signals]);
  const [verification, setVerification] = useState<Verification | null>(null); const [verifyError, setVerifyError] = useState("");
  async function reverify() { setVerifyError(""); try { setVerification(await api<Verification>(`/api/filings/${accession}/reverify`, { method: "POST" })); } catch (reason) { setVerifyError(reason instanceof ApiError ? reason.message : "Re-verification failed."); } }
  if (!resource.data) return <main className="page">{resource.loading ? <p className="loading">Loading filing evidence…</p> : <div className="notice">{resource.error?.message}</div>}</main>;
  const detail = resource.data; const filing = detail.filing; const audit = verification ?? detail.verification;
  return <main className="page"><button className="button ghost" onClick={() => navigate(-1)}>← Back to brief</button><header className="section"><h1 className="page-title">{filing.ticker} — {filing.form} filed {filing.filed}</h1><div className="filing-heading"><SeverityBadge severity={filing.severity} />{filing.posture ? <PosturePill posture={filing.posture} /> : <span className="mono faint">watch — company-level read, no signal</span>}<OwnedWatchTag owned={filing.owned} /></div></header>{filing.manual_review && <div className="notice">⚠ manual review required</div>}{detail.insufficient_reason && <div className="notice neutral"><PosturePill posture="insufficient_data" /> {detail.insufficient_reason}</div>}
    <section className="section"><h2 className="section-kicker">Material items</h2>{filing.material_items.map((item, index) => <p key={index}>{item.headline} <em className="faint">({item.event_type})</em></p>)}</section>
    <section className="section"><h2 className="section-kicker">Red flags</h2>{filing.flags.length ? filing.flags.map(flag => <RedFlagRow flag={flag} key={flag.code} />) : <p className="empty-line">No critical or high-severity findings.</p>}</section>
    {detail.what_changed.map((row, index) => <section className="section" key={index}><h2 className="section-kicker">Transmission channels</h2><div className="channels">{row.channels.map(channel => <span className={`channel ${channel.direction}`} key={channel.label}>{channel.label} ({channel.direction}{channel.magnitude ? `, ${channel.magnitude}` : ""})</span>)}</div><p className="gln">Guidance: {row.guidance} · Liquidity: {row.liquidity} · Net: {row.net}</p></section>)}
    {filing.owned && <section className="section"><h2 className="section-kicker">Thesis impact</h2>{detail.thesis_impact.length ? detail.thesis_impact.map(row => <p key={row.ticker}><strong>{row.ticker}:</strong> {row.no_thesis ? "No thesis provided — RipplX degrades gracefully without one." : `thesis ${row.verdict}`}</p>) : <p className="empty-line">No thesis impact assessed.</p>}</section>}
    <section className="section"><div className="page-header"><h2 className="section-kicker">Verified numbers</h2><Link className="button" to={`/companies/${filing.ticker}${demo ? "?demo=1" : ""}`}>Full company view</Link></div>{detail.verified_numbers?.empty ? <p className="empty-line">{detail.verified_numbers.empty}</p> : detail.verified_numbers ? <MetricTable rows={detail.verified_numbers.rows} /> : <p className="empty-line">no verified financials yet (XBRL facts insufficient or not yet ingested).</p>}</section>
    {!demo && <section className="section audit"><div className="page-header"><h2 className="section-kicker">Verification audit</h2><button className="button" onClick={reverify}>Re-verify</button></div>{verifyError && <div className="field-error">{verifyError}</div>}{audit ? <><PosturePill posture={audit.verdict === "FAIL" ? "critical_review" : audit.verdict === "PASS_WITH_WARNINGS" ? "risk_review" : "monitor"} /><div className="channels">{audit.checks.map(check => <span className="channel" key={check.check_id}>{check.check_id}: {check.verdict}</span>)}</div></> : <p className="empty-line">No verification result yet.</p>}</section>}
    {detail.shadow_signal && <ShadowRegion signals={[detail.shadow_signal]} />}<DisclaimerFooter text={detail.disclaimer} /></main>;
}
