import { NavLink, Outlet, useLocation } from "react-router-dom";

const links = [
  ["/brief", "The Brief", "Digest & alerts"],
  ["/holdings", "Holdings", "Portfolio & watch"],
  ["/track-record", "Track record", "Signal audit"],
] as const;

export function AppShell() {
  const location = useLocation();
  const demoSuffix = new URLSearchParams(location.search).get("demo") === "1" ? "?demo=1" : "";
  return <div className="app">
    <nav className="rail" aria-label="Main navigation">
      <div className="brand"><strong>RipplX</strong><span>Filing intelligence</span></div>
      <div className="nav-list">
        {links.map(([to, label, sublabel]) => <NavLink key={to} to={`${to}${demoSuffix}`} className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>{label}<span>{sublabel}</span></NavLink>)}
      </div>
      <div className="nav-foot"><NavLink to={`/settings${demoSuffix}`} className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>⚙ Settings<span>Keys & preferences</span></NavLink></div>
    </nav>
    <div className="content"><Outlet /></div>
  </div>;
}
