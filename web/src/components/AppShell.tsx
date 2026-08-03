import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { useBootstrap } from "../context/BootstrapContext";

const links = [
  { to: "/today", label: "Today", sublabel: "What needs attention", glyph: "01" },
  { to: "/research", label: "Research", sublabel: "Before you buy", glyph: "02" },
  { to: "/companies", label: "Companies", sublabel: "Watchlist & holdings", glyph: "03" },
  { to: "/alerts", label: "Alerts", sublabel: "Monitoring history", glyph: "04" },
] as const;

export function AppShell() {
  const location = useLocation();
  const { bootstrap } = useBootstrap();
  const demo = new URLSearchParams(location.search).get("demo") === "1";
  const demoSuffix = demo ? "?demo=1" : "";
  const showcaseDate = bootstrap.showcase_updated_at
    ? new Date(bootstrap.showcase_updated_at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
    : null;
  return <div className="app">
    <nav className="rail" aria-label="Main navigation">
      <Link className="brand" to={`/brief${demoSuffix}`}><span className="brand-copy"><strong>RipplX</strong><small>Filing intelligence</small></span></Link>
      <div className="nav-list">
        <span className="nav-label">{demo ? "SEC showcase" : "Workspace"}</span>
        {links.map(link => <NavLink key={link.to} to={`${link.to}${demoSuffix}`} className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}><span className="nav-glyph">{link.glyph}</span><span className="nav-copy">{link.label}<small>{link.sublabel}</small></span><span className="nav-arrow" aria-hidden="true">›</span></NavLink>)}
        <NavLink to={`/about${demoSuffix}`} className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}><span className="nav-glyph">05</span><span className="nav-copy">About<small>How it works</small></span><span className="nav-arrow" aria-hidden="true">›</span></NavLink>
      </div>
      <div className="rail-trust"><p><strong>Trust-first analysis</strong><small>Exact SEC evidence.<br />Deterministic checks.</small></p></div>
      <div className="nav-foot">{demo
        ? <NavLink to="/signin" className="nav-link"><span className="nav-glyph">→</span><span className="nav-copy">Start your own<small>Track your tickers</small></span><span className="nav-arrow" aria-hidden="true">›</span></NavLink>
        : <NavLink to="/settings" className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}><span className="nav-glyph">05</span><span className="nav-copy">Settings<small>Notifications &amp; providers</small></span><span className="nav-arrow" aria-hidden="true">›</span></NavLink>}</div>
    </nav>
    <div className="content">
      {demo && <div className="topbar">
        <span className="topbar-note">{bootstrap.showcase_source === "sec_cache" ? `Cached SEC showcase${showcaseDate ? ` · refreshed ${showcaseDate}` : ""}` : "Bundled SEC showcase"} · no account needed</span>
        <Link className="button primary" to="/signin">Sign in</Link>
      </div>}
      <Outlet />
    </div>
  </div>;
}
