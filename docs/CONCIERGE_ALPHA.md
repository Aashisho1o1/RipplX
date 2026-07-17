# Concierge alpha operating guide

## Purpose

Validate one narrow promise before expanding the product:

> Add your tickers. When a filing arrives, get up to three important changes, exact evidence,
> and a handful of verified financial deltas.

This is an early public prototype, not an automated growth campaign and not investment advice.
Users may sign in directly; deterministic publication checks remain the release gate. The operator
samples results and reviews reported failures rather than manually approving every page view.

## Cohort

- Recruit 5–10 self-directed investors.
- Each participant tracks 3–10 tickers.
- Prefer people who already monitor individual companies and can judge whether a filing change
  mattered.
- Collect ticker symbols only. Do not request shares, cost basis, target weights, portfolio
  value, investment thesis, brokerage credentials, or account exports.

Recruitment and feedback outreach remain human, explicitly authorized activities. finwatch must not
scrape contact data, send marketing messages, post automatically, or contact anyone on the
operator's behalf. Public signup sends only a code requested by the person entering that email.
Obtain consent before separately recording participant feedback.

## Before the first participant

1. Back up and recreate any pre-v5 database; the prototype intentionally has no compatibility
   migration ladder.
2. For a hosted alpha, verify TLS, the signing secret, Resend sender/domain, exact host allowlist,
   operator SEC User-Agent, persistent `/data` volume, and a restorable backup.
3. Confirm two test accounts cannot read or mutate each other's watchlists, preferences, filings,
   metrics, jobs, or session API keys. Each account is capped at 25 tickers.
4. Use one evaluated `openai/` model through `FINWATCH_MODEL`.
5. Run the recorded test suite and representative-filing evaluation set.
6. Prepare a private feedback log using participant aliases. Do not put API keys, login codes,
   session cookies,
   full portfolio data, or unnecessary personal information in it.

## Review checklist for sampled or reported digests

Use this checklist while sampling output and whenever a participant reports a problem:

1. Confirm the ticker, form, accession, filing date, and SEC URL identify the intended newest
   supported filing.
2. Confirm the run reached a terminal verified state and every required blocking verification
   and presentation-integrity check passed.
3. Confirm there are no more than three findings. Each headline must be qualitative and each
   finding must include at least one exact quotation.
4. Click every SEC citation. Confirm the quotation is verbatim, belongs to the stated section and
   accession, and actually supports the headline without relying on omitted context.
5. Inspect every displayed number. It must be either inside the exact quotation or in a starter
   metric row with a deterministic computation source and computed-as-of date. Confirm share-count
   changes use neutral direction language and leverage is labeled as the net-debt /
   (operating income + D&A) proxy—not reported EBITDA.
6. Confirm routine filings are allowed to be quiet. Do not add an alert manually merely to make
   the digest look useful.
7. If any blocking check failed, confirm all LLM-derived findings are withheld. Never waive the
   verifier, copy failed model output into a message, or present a partial result as verified.
8. Read the final browser view or Markdown artifact the participant will actually receive—not a
   raw model response, database row, or internal diagnostic.

If a wrong citation, unsupported finding, untraceable number, advice-like instruction, secret, or
unwithheld verifier failure appears, withhold or remove that run. Preserve only the minimum safe
diagnostic information, record the accession and failure class, and fix or quarantine the path.

## Participant session

1. Ask the participant to sign in with their email code and add 3–10 ticker symbols. Explain that
   their email, private watchlist/preferences, public SEC filings, and generated analysis are stored
   in the prototype database.
2. Let the participant add their matching provider API key in Settings, then sync and analyze the
   newest filing. Explain that the key is server-memory-only and is lost on restart.
3. Sample the resulting digest with the review checklist when observing the session.
4. Let the participant explore the canonical digest and decide what to click; do not
   coach them toward a positive answer.
5. Ask only the seven questions below, in this order, and capture the response as faithfully as
   practical.

## The seven feedback questions

1. Did this surface a filing you would otherwise have missed?
2. Was the highlighted change actually important?
3. Was anything noisy or misleading?
4. Did you click the citation?
5. Did the evidence make you trust the result?
6. Would you return after the next filing?
7. What did you wish it explained?

Do not ask whether participants want Piotroski, P3, cross-holding transmission, signals,
portfolio accounting, or any other deferred feature. People often approve feature descriptions;
the useful evidence is whether they repeatedly use and trust the basic digest.

## Minimal feedback log

For each reviewed filing, record only:

- participant alias and session date;
- number of tracked tickers;
- ticker, accession, and form reviewed;
- whether the digest was shared or withheld after manual review;
- the participant's answers to the seven questions; and
- a short operator note for any concrete correctness, noise, citation, or usability defect.

Do not infer a participant's portfolio size or investment intent. Do not turn free-text feedback
into an investment profile. Set a deletion date for the feedback log and remove participant data
on request.

## End-of-alpha decision

After 5–10 participants have experienced at least one real filing, review behavior rather than
feature enthusiasm:

- Did the product repeatedly surface filings participants would have missed?
- Were the selected changes actually material and acceptably quiet?
- Did participants click citations and report greater trust because of the evidence?
- Did any misleading number, unsupported conclusion, or failed-output leak reach a participant?
- Did participants return, or clearly intend to return, for the next filing?

Do not restore deferred scope merely because it already exists in the repository. P2, P3,
signals, portfolio accounting, historical replay, extended metrics, and multi-provider routing
must earn their way back through observed user behavior.
