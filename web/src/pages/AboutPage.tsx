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
    kicker: "Hallucination containment",
    title: "The model chooses what matters. It never gets to invent a fact.",
    body: "The language model selects and summarises; it cannot author a number, cannot widen its own permissions, and cannot publish a quotation that is not in the filing. That boundary is enforced in code and re-checked on the final output, not requested in a prompt.",
  },
  {
    kicker: "Honest failure",
    title: "Silence is reported, not implied.",
    body: "A routine filing publishes as a routine filing. A filing whose candidate findings were all rejected says so. A run that never completed is shown in its own bucket. You are never told 'nothing important changed' when the truth is 'nothing was checked'.",
  },
] as const;

const ROADMAP = [
  { title: "Filings that find you", body: "Periodic delivery to your inbox — a brief when a filing earns your attention, and a short all-clear when it does not, so you never have to remember to visit." },
  { title: "Connected research", body: "A bounded synthesis pass connects published filing changes, verified financial context, and peers without weakening the evidence rules." },
  { title: "Connect your brokerage", body: "Read only the list of tickers you hold — never balances, positions, or credentials — so your watchlist builds itself." },
  { title: "Portfolio-level review", body: "The same verified treatment applied across everything you follow, rather than one filing at a time." },
] as const;

export function AboutPage() {
  return <main className="page">
    <header className="page-header">
      <div>
        <p className="page-eyebrow">About RipplX</p>
        <h1 className="page-title">Filing intelligence that has to prove itself.</h1>
        <p className="page-subtitle">
          Track the companies you own. When their newest SEC filing arrives, see at most three
          important changes, the exact evidence behind each one, and six financial deltas computed
          deterministically from SEC data.
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
      <SectionHeader index="03 · Ahead" title="Where this is going" />
      <p className="metric-caption">
        Planned, not shipped. Listed here so you can judge the direction, not so it can be claimed.
      </p>
      <div className="about-grid">
        {ROADMAP.map(item => <article className="about-card muted-card" key={item.title}>
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
