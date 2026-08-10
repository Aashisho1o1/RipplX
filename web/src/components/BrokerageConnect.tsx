// Brokerage linking is a founding-plan feature, and the connection itself is not built yet.
// The control is therefore inert on purpose: an enabled button, or a "you're on the list"
// confirmation for a list that does not exist, would be the one failure mode RipplX cannot
// afford — a plausible claim the product cannot back up. Enable the button here once the
// connect endpoint ships.
const BROKERS = [
  "Robinhood", "Charles Schwab", "Fidelity", "E*TRADE",
  "Vanguard", "Interactive Brokers", "Webull", "Tastytrade",
] as const;

export function BrokerageConnect({ index, paid }: { index: string; paid: boolean }) {
  return <section className="surface settings-section">
    <div className="settings-heading">
      <span className="settings-index">{index}</span>
      <div>
        <h2>Brokerage</h2>
        <p>Track what you already own instead of typing in every ticker.</p>
      </div>
      <span className={`connection-status${paid ? " connected" : ""}`}>
        <i aria-hidden="true" />{paid ? "Included in your plan" : "Founding plan"}
      </span>
    </div>

    <p className="metric-caption">
      Link a brokerage and each position becomes a tracked company. New 10-K, 10-Q, and 8-K
      filings for the things you actually hold arrive in Today with the same exact SEC
      quotations and deterministic numbers as a ticker you added by hand.
    </p>

    <ul className="about-list">
      <li>Read-only. RipplX reads balances and positions. It cannot place or cancel an order, move cash, or change anything at your broker.</li>
      <li>You sign in on your broker’s own page. Your brokerage credentials never reach RipplX.</li>
      <li>No custody. RipplX never holds your funds or securities, and you can disconnect at any time.</li>
    </ul>

    <div className="ticker-strip">
      {BROKERS.map(broker => <span className="ticker-chip" key={broker}>{broker}</span>)}
      <span>and more</span>
    </div>

    <div className="actions brokerage-actions">
      <button type="button" className="button primary" disabled>Connect a brokerage</button>
    </div>
    <p className="helper">{paid
      ? "Your plan includes brokerage connections. Linking is not switched on in this build yet — RipplX will email you when yours is ready."
      : "Connecting a brokerage is part of the founding plan. Tracking tickers by hand stays free."}</p>
  </section>;
}
