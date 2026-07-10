-- One tracked holding/watchlist row per issuer.  database.py performs a
-- fail-closed duplicate check before this migration; it never guesses which
-- pre-existing row contains the user's intended data.
CREATE UNIQUE INDEX ux_holdings_cik ON holdings(cik);
