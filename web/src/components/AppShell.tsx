import { NavLink, Outlet, useLocation } from "react-router-dom";

const links = [
  { to: "/brief", label: "Filing brief", sublabel: "What changed", glyph: "01" },
  { to: "/companies", label: "Companies", sublabel: "Tracked tickers", glyph: "02" },
] as const;

export function AppShell() {
  const location = useLocation();
  const demoSuffix = new URLSearchParams(location.search).get("demo") === "1" ? "?demo=1" : "";
  return <div className="app">
    <nav className="rail" aria-label="Main navigation">
      <div className="brand"><span className="brand-mark" aria-hidden="true"><i /><i /><i /></span><span className="brand-copy"><strong>RipplX</strong><small>Filing intelligence</small></span></div>
      <div className="nav-list">
        <span className="nav-label">Workspace</span>
        {links.map(link => <NavLink key={link.to} to={`${link.to}${demoSuffix}`} className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}><span className="nav-glyph">{link.glyph}</span><span className="nav-copy">{link.label}<small>{link.sublabel}</small></span><span className="nav-arrow" aria-hidden="true">›</span></NavLink>)}
      </div>
      <div className="rail-trust"><span className="status-dot" aria-hidden="true" /><p><strong>Trust-first analysis</strong><small>Exact SEC evidence. Deterministic checks.</small></p></div>
      <div className="nav-foot"><NavLink to={`/settings${demoSuffix}`} className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}><span className="nav-glyph">03</span><span className="nav-copy">Settings<small>Keys & preferences</small></span><span className="nav-arrow" aria-hidden="true">›</span></NavLink></div>
    </nav>
    <div className="content"><Outlet /></div>
  </div>;
}
