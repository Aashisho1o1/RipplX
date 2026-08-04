import { Link } from "react-router-dom";
import { DisclaimerFooter } from "../components/DisclaimerFooter";
import { SectionHeader } from "../components/SectionHeader";

const disclaimer = "Educational analysis of public information for the portfolio owner's own decision-making. Not individualized investment advice. Data may be incomplete or delayed.";

const PILLARS = [
  {
    kicker: "Deterministic math",
    title: "Every number comes from SEC XBRL and Python — never from a language model.",
    body: "The six financial deltas are computed by versioned formulas straight from the issuer's own XBRL facts. Each row carries the formula version, the date it was computed as of, and the exact facts that fed it. Open any row and you see the arithmetic, not a claim about it.",
  },
  {
    kicker: "Exact evidence",
    title: "A finding is inseparable from the sentence that proves it.",
    body: "Every published change carries one to three verbatim SEC quotations. The character offsets are derived on the server from the stored filing, not supplied by the model, and each quote is shown with the SHA-256 of the section it came from and a link to the filing on EDGAR.",
  },
  {
    kicker: "The publication gate",
    title: "Unsupported claims cannot get out.",
    body: "Before anything is published, deterministic checks run: quotations must match the filing byte for byte, headlines may not contain numbers or advice, and stated directions must agree with the computed metric. A finding that fails is dropped with a typed reason — the surviving findings and the verified numbers still publish.",
  },
  {
    kicker: "Interpretation boundary",
    title: "The evidence can be verified. A possible effect cannot be proven automatically.",
    body: "RipplX verifies source quotations and deterministic calculations. When an optional model pass describes a possible driver or financial effect, the interface labels it as conditional and shows its assumptions and limitations. It is context for your review, not a prediction.",
  },
  {
    kicker: "Honest failure",
    title: "Silence is reported, not implied.",
    body: "A routine filing publishes as a routine filing. A filing whose candidate findings were all rejected says so. A run that never completed is shown in its own bucket. You are never told 'nothing important changed' when the truth is 'nothing was checked'.",
  },
] as const;

const CURRENT_PRODUCT = [
  { title: "Research before buying", body: "Enter a U.S. ticker to assemble the newest supported SEC filings, up to three AI-selected changes with exact quotations, and six deterministic financial trends." },
  { title: "Monitor new filings", body: "Track a company, classify new supported filings by attention level, and choose immediate, same-week, weekly, or in-app-only notification behavior." },
  { title: "Compare local peers", body: "Review SIC-derived peer candidates when comparable companies have already been ingested. RipplX explains the match without calling it an investment recommendation." },
] as const;

export function AboutPage() {
  return <main className="page">
    <header className="page-header">
      <div>
        <p className="page-eyebrow">About RipplX</p>
        <h1 className="page-title">Filing intelligence that has to prove itself.</h1>
        <p className="page-subtitle">
          Research and monitor U.S. companies through their SEC filings. See up to three
          AI-selected changes worth reviewing, the exact source quotations behind them, and six
          financial trends computed deterministically from SEC data.
        </p>
      </div>
    </header>

    <section className="section">
      <SectionHeader index="01 · Principles" title="How it works" />
      <p className="metric-caption">
        RipplX is built around one idea: an AI may decide what is worth your attention, but it may
        never be the reason you believe a fact.
      </p>
      <div className="about-grid">
        {PILLARS.map(pillar => <article className="about-card" key={pillar.kicker}>
          <p className="section-kicker">{pillar.kicker}</p>
          <h3>{pillar.title}</h3>
          <p>{pillar.body}</p>
        </article>)}
      </div>
    </section>

    <section className="section">
      <SectionHeader index="02 · Scope" title="What this is not" />
      <p className="metric-caption">
        Being explicit about the boundary is part of the product.
      </p>
      <ul className="about-list">
        <li>Not an investment adviser. RipplX never tells you to buy, sell, hold, or trim anything.</li>
        <li>Not a portfolio manager. It does not ask for shares, cost basis, or target weights.</li>
        <li>Not a price or market-data service. It reads filings, not tickers on a chart.</li>
        <li>
          Verification proves that displayed evidence is exact and that displayed numbers come from
          allowed sources. It does not prove the model's judgement of importance is correct — which
          is why findings are labelled AI-selected.
        </li>
      </ul>
    </section>

    <section className="section">
      <SectionHeader index="03 · Product" title="What works today" />
      <p className="metric-caption">
        The current prototype stays focused on SEC evidence, verified financial trends, and filing monitoring.
      </p>
      <div className="about-grid">
        {CURRENT_PRODUCT.map(item => <article className="about-card muted-card" key={item.title}>
          <h3>{item.title}</h3>
          <p>{item.body}</p>
        </article>)}
      </div>
    </section>

    <section className="empty-invitation">
      <p className="section-kicker">Start here</p>
      <h2>See it on a real filing.</h2>
      <p>The public showcase reads cached SEC filings and XBRL facts, with a bundled SEC fallback when no operator refresh is available. Opening it never triggers a live external request.</p>
      <div className="actions">
        <Link className="button primary" to="/brief?demo=1">Open the SEC showcase</Link>
        <Link className="button" to="/companies">Track your own ticker</Link>
      </div>
    </section>

    <DisclaimerFooter text={disclaimer} />
  </main>;
}
