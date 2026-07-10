"""P0 preprocessor: form routing, section detection, furnished, diff, persistence."""
from __future__ import annotations

import re
from pathlib import Path

from finwatch.db import Filing, Repo, init_db
from finwatch.preprocess import (
    Preprocessor,
    base_form,
    diff_risk_factors,
    form_family,
    furnishing_present,
    html_to_text,
    is_amendment,
    split_8k,
    split_10k,
    split_10q,
)

FX = Path(__file__).parent / "fixtures"
TENQ = (FX / "tenq_sample.html").read_text(encoding="utf-8")
TENK = (FX / "tenk_sample.html").read_text(encoding="utf-8")
EIGHTK = (FX / "eightk_sample.html").read_text(encoding="utf-8")


# ---- form helpers ----------------------------------------------------------
def test_form_helpers():
    assert is_amendment("10-K/A") and not is_amendment("10-K")
    assert base_form("10-Q/A") == "10-Q"
    assert form_family("10-Q") == "10-Q"
    assert form_family("8-K") == "8-K"
    assert form_family("10-K405") == "10-K"


# ---- ToC skipping ----------------------------------------------------------
def test_toc_anchor_links_are_skipped():
    doc = html_to_text(TENQ)
    flags = [doc.is_link_at(m.start()) for m in re.finditer(r"(?im)^[ \t]*Item[ \t]+2\b", doc.text)]
    assert any(flags), "ToC 'Item 2.' link should be present"
    assert any(not f for f in flags), "the real non-link 'Item 2.' header should be present"


# ---- 10-Q routing (Definition of Done) -------------------------------------
def test_10q_mdna_routes_from_part1_item2():
    secs = {s.section_key: s for s in split_10q(html_to_text(TENQ))}
    assert "mdna" in secs
    assert "Management" in secs["mdna"].title
    assert "Net sales increased" in secs["mdna"].text  # real body, not the ToC entry


def test_10q_full_section_set_in_order():
    keys = [s.section_key for s in split_10q(html_to_text(TENQ))]
    assert keys == ["financials", "mdna", "market_risk", "controls", "legal", "risk_factor_changes"]


def test_10q_part2_item2_unregistered_is_not_mapped():
    secs = split_10q(html_to_text(TENQ))
    # Part II Item 2 (Unregistered Sales) must not become a second 'mdna' or any canonical key
    assert [s.section_key for s in secs].count("mdna") == 1
    assert not any("Unregistered" in s.text for s in secs if s.section_key == "mdna")


# ---- 10-K routing ----------------------------------------------------------
def test_10k_full_section_set():
    keys = [s.section_key for s in split_10k(html_to_text(TENK))]
    # Item 8 additionally yields an auditor_report sub-section (its heading is present).
    assert keys == ["business", "risk_factors", "legal", "mdna", "market_risk",
                    "financials", "auditor_report", "controls"]


def test_10k_item8_emits_auditor_report_subsection():
    secs = {s.section_key: s for s in split_10k(html_to_text(TENK))}
    assert "auditor_report" in secs
    assert "Report of Independent Registered Public Accounting Firm" in secs["auditor_report"].text
    # sub-section sits inside the Item 8 (financials) umbrella span
    fin = secs["financials"]
    assert fin.char_start <= secs["auditor_report"].char_start < fin.char_end


def test_10k_business_is_item1_not_1a():
    secs = {s.section_key: s for s in split_10k(html_to_text(TENK))}
    assert "designs, manufactures" in secs["business"].text
    assert "Risk Factors" not in secs["business"].text  # ends before Item 1A


def test_10k_risk_factors_stops_at_next_item_boundary():
    secs = {s.section_key: s for s in split_10k(html_to_text(TENK))}
    rf = secs["risk_factors"]
    assert "intense competition" in rf.text
    assert "Unresolved Staff Comments" not in rf.text  # Item 1B is the boundary
    assert "Properties" not in rf.text


def test_section_hash_matches_text():
    import hashlib
    for s in split_10k(html_to_text(TENK)):
        assert s.text_sha256 == hashlib.sha256(s.text.encode()).hexdigest()
        assert s.text == html_to_text(TENK).text[s.char_start:s.char_end]


# ---- 8-K -------------------------------------------------------------------
def test_8k_items_split_and_furnished_flag():
    secs = {s.section_key: s for s in split_8k(html_to_text(EIGHTK))}
    assert set(secs) == {"item_2_02", "item_9_01"}
    assert secs["item_2_02"].is_furnished is True   # 2.02 + furnishing language
    assert secs["item_9_01"].is_furnished is False


def test_8k_hidden_inline_xbrl_is_filtered():
    doc = html_to_text(EIGHTK)
    assert "CommonStockMember" not in doc.text
    assert "UNITED STATES" in doc.text


def test_furnishing_detection_variants():
    assert furnishing_present('shall not be deemed "filed"')
    assert furnishing_present("This exhibit is being furnished herewith.")
    assert furnishing_present("information shall not be deemed to be filed")
    assert not furnishing_present("This report is filed with the Commission.")


# ---- plain-text filing -----------------------------------------------------
def test_plain_text_filing_routes():
    text = (
        "Item 1. Business\nWe make devices.\n"
        "Item 1A. Risk Factors\nMany risks exist for our business.\n"
        "Item 7. Management's Discussion and Analysis\nResults improved this year.\n"
    )
    keys = [s.section_key for s in split_10k(html_to_text(text))]
    assert {"business", "risk_factors", "mdna"} <= set(keys)


# ---- risk-factor diff ------------------------------------------------------
def test_risk_factor_diff_added_removed_modified():
    prior = ("Risk A: markets are volatile and unpredictable.\n"
             "Risk B: our supply chains may fail.\n"
             "Risk C: currency swings can hurt results.")
    curr = ("Risk A: markets are volatile and unpredictable.\n"
            "Risk B: our supply chains may fail badly and frequently under stress now.\n"
            "Risk D: new cybersecurity threats have emerged.")
    d = diff_risk_factors(prior, curr)
    assert any("Risk D" in p.text for p in d.added)
    assert any("Risk C" in p.text for p in d.removed)
    assert any("Risk B" in m.prior.text for m in d.modified)
    assert not d.is_empty()


def test_risk_factor_diff_identical_is_empty():
    t = "Risk A: something material.\nRisk B: a longer sentence about the supply chain."
    assert diff_risk_factors(t, t).is_empty()


# ---- orchestrator: persistence + FTS + status ------------------------------
def _repo_with_filing(**kw) -> Repo:
    repo = Repo(init_db(":memory:"))
    repo.upsert_filing(Filing(**kw))
    return repo


def test_preprocess_persists_sections_and_syncs_fts():
    repo = _repo_with_filing(accession_number="a-1", cik="1", form_type="10-Q",
                             filed_at="2024-08-02", period_of_report="2024-06-29")
    pp = Preprocessor(repo, now_fn=lambda: "2026-07-04T00:00:00")
    res = pp.preprocess_html(accession_number="a-1", cik="1", form_type="10-Q",
                             filed_at="2024-08-02", period_of_report="2024-06-29", html=TENQ)
    assert [s.section_key for s in res.sections][:2] == ["financials", "mdna"]
    assert repo.get_filing("a-1").status == "sectioned"
    assert repo.get_filing("a-1").processed_at == "2026-07-04T00:00:00"
    assert len(repo.list_filing_sections("a-1")) == 6

    def fts_hits():
        return repo.conn.execute(
            "SELECT count(*) FROM section_fts WHERE section_fts MATCH 'Management'"
        ).fetchone()[0]

    first = fts_hits()
    assert first >= 1
    # reprocess: FTS must stay consistent (no orphaned rows)
    pp.preprocess_html(accession_number="a-1", cik="1", form_type="10-Q",
                       filed_at="2024-08-02", period_of_report="2024-06-29", html=TENQ)
    assert fts_hits() == first
    assert len(repo.list_filing_sections("a-1")) == 6


def test_amendment_links_to_original():
    repo = Repo(init_db(":memory:"))
    repo.upsert_filing(Filing(accession_number="orig", cik="1", form_type="10-K",
                              filed_at="2024-11-01", period_of_report="2024-09-28"))
    repo.upsert_filing(Filing(accession_number="amend", cik="1", form_type="10-K/A",
                              filed_at="2024-12-01", period_of_report="2024-09-28",
                              is_amendment=1))
    pp = Preprocessor(repo, now_fn=lambda: "t")
    res = pp.preprocess_html(accession_number="amend", cik="1", form_type="10-K/A",
                             filed_at="2024-12-01", period_of_report="2024-09-28", html=TENK)
    assert res.amends_accession == "orig"
    assert repo.get_filing("amend").amends_accession == "orig"


def test_part_three_only_10ka_routes_as_explicit_amendment_section():
    html = (
        "<p>FORM 10-K/A</p><p>Explanatory Note</p>"
        "<p>This amendment includes Items 10 through 14 of Part III.</p>"
        "<p>Item 10. Directors and Corporate Governance.</p>"
    )
    repo = _repo_with_filing(
        accession_number="amend-only",
        cik="1",
        form_type="10-K/A",
        filed_at="2025-04-28",
        is_amendment=1,
    )

    result = Preprocessor(repo, now_fn=lambda: "t").preprocess_html(
        accession_number="amend-only",
        cik="1",
        form_type="10-K/A",
        filed_at="2025-04-28",
        period_of_report=None,
        html=html,
    )

    assert [section.section_key for section in result.sections] == ["amendment"]
    assert "Items 10 through 14" in result.sections[0].text
    assert repo.get_filing("amend-only").status == "sectioned"


def test_plain_text_toc_does_not_capture_the_stub():
    # A non-hyperlinked ToC (older HTML / .txt filings): every 'Item N' line is a
    # non-link candidate. The real body must win over the one-line ToC stub.
    plain = (
        "TABLE OF CONTENTS\n"
        "Item 1. Business 3\n"
        "Item 1A. Risk Factors 10\n"
        "Item 7. Management's Discussion and Analysis 30\n"
        "Item 8. Financial Statements 60\n"
        "Item 1. Business\nWe design and sell devices to consumers worldwide.\n"
        "Item 1A. Risk Factors\nOur business faces intense competition and supply risk.\n"
        "Item 7. Management's Discussion and Analysis\n"
        "Net sales increased sharply this fiscal year across all regions.\n"
        "Item 8. Financial Statements\nConsolidated balance sheets are presented.\n"
    )
    secs = {s.section_key: s for s in split_10k(html_to_text(plain))}
    assert "Net sales increased" in secs["mdna"].text  # real body, not the '... 30' stub


def test_prose_cross_reference_not_mistaken_for_header():
    prose = (
        "Item 1. Business\nWe design devices.\n"
        "Item 7 contains additional Management's Discussion of these operations, noted later.\n"
        "Item 1A. Risk Factors\nRisks exist here for us.\n"
        "Item 7. Management's Discussion and Analysis\nActual MD&A body: revenue rose strongly.\n"
    )
    secs = {s.section_key: s for s in split_10k(html_to_text(prose))}
    assert "Actual MD&A body" in secs["mdna"].text


def test_br_tag_injects_line_break_for_header_detection():
    doc = html_to_text(
        "<div><b>Overview.</b><br/>Item 7A. Quantitative and Qualitative Disclosures "
        "About Market Risk</div>"
    )
    assert re.search(r"(?im)^Item\s+7A\b", doc.text) is not None


def test_inline_tags_do_not_split_a_line():
    # Regression: selectolax returns a fresh wrapper per .parent access, so the
    # block-boundary check must use value equality (!=), not identity (is not).
    # A sentence split by inline formatting / inline-XBRL must stay on one line;
    # only a real block boundary (the second <p>) may inject a newline.
    doc = html_to_text(
        "<p>Revenue was $<ix>5,234</ix> million, up from $<ix>4,100</ix> "
        "on a <b>going concern</b> basis.</p>"
        "<p>Next paragraph.</p>"
    )
    # inline-XBRL numbers stay attached to their currency symbol and sentence
    assert "$5,234 million" in doc.text
    assert "up from $4,100" in doc.text
    # a phrase spanning an inline <b> is not shredded (lexicon regexes rely on this)
    assert "going concern" in doc.text
    # the whole first sentence is one line (no injected newlines inside the block)
    assert "Revenue was $5,234 million, up from $4,100 on a going concern basis." in doc.text
    # but a genuine block boundary still breaks
    assert "\nNext paragraph." in doc.text


def test_8k_furnishing_is_scoped_per_item():
    # 2.02 filed (no legend in its span), 7.01 furnished (legend in its span)
    html = (
        "<html><body>"
        "<p><span style='font-weight:700'>Item 2.02 Results of Operations "
        "and Financial Condition.</span></p>"
        "<p>The Company reported results, filed herewith and incorporated by reference.</p>"
        "<p><span style='font-weight:700'>Item 7.01 Regulation FD Disclosure.</span></p>"
        "<p>The information in this Item 7.01 shall not be deemed \"filed\" "
        "for purposes of Section 18.</p>"
        "</body></html>"
    )
    secs = {s.section_key: s for s in split_8k(html_to_text(html))}
    assert secs["item_2_02"].is_furnished is False  # filed, no legend in its span
    assert secs["item_7_01"].is_furnished is True    # furnished legend in its span


def test_8k_item_number_and_title_may_be_on_separate_lines():
    html = (
        "<html><body>"
        "<div>Item\u20095.02.</div>"
        "<div>Departure of Directors or Certain Officers; Election of Directors.</div>"
        "<p>A director will not stand for re-election.</p>"
        "</body></html>"
    )
    sections = {section.section_key: section for section in split_8k(html_to_text(html))}
    assert "item_5_02" in sections
    assert "Departure of Directors" in sections["item_5_02"].title


def test_8k_dash_separated_single_line_item_header_is_sectioned():
    # SEC filers commonly separate the item token from its title with a dash on one
    # line: "Item 5.02 - Departure...". The header must still be recognised.
    for sep in ("-", "–", "—", ":"):
        html = (
            "<html><body>"
            f"<p><b>Item 5.02 {sep} Departure of Directors or Certain Officers.</b></p>"
            "<p>A director will not stand for re-election.</p>"
            "</body></html>"
        )
        sections = {s.section_key: s for s in split_8k(html_to_text(html))}
        assert "item_5_02" in sections, f"dropped with separator {sep!r}"
        assert "Departure of Directors" in sections["item_5_02"].title


def test_10k_item_number_and_title_may_be_on_separate_lines():
    # Two-cell table layout: "Item 7." in one cell, its title in the next, so after
    # flattening the token and title are on separate lines. MD&A must not be dropped.
    html = (
        "<html><body>"
        "<div>Item 7.</div>"
        "<div>Management's Discussion and Analysis of Financial Condition "
        "and Results of Operations.</div>"
        "<p>Net sales increased 8% year over year driven by services.</p>"
        "<div>Item 7A.</div>"
        "<div>Quantitative and Qualitative Disclosures About Market Risk.</div>"
        "<p>We are exposed to interest rate risk.</p>"
        "</body></html>"
    )
    secs = {s.section_key: s for s in split_10k(html_to_text(html))}
    assert "mdna" in secs, "split-line 'Item 7.' header dropped MD&A"
    assert "Management" in secs["mdna"].title
    assert "Net sales increased" in secs["mdna"].text
    assert "market_risk" in secs  # the split-line 'Item 7A.' also routes


def test_10q_item_number_and_title_may_be_on_separate_lines():
    html = (
        "<html><body>"
        "<div>Part I - Financial Information</div>"
        "<div>Item 2.</div>"
        "<div>Management's Discussion and Analysis of Financial Condition "
        "and Results of Operations.</div>"
        "<p>Net sales increased 8% year over year.</p>"
        "</body></html>"
    )
    secs = {s.section_key: s for s in split_10q(html_to_text(html))}
    assert "mdna" in secs, "split-line Part I 'Item 2.' header dropped MD&A"
    assert "Management" in secs["mdna"].title
    assert "Net sales increased" in secs["mdna"].text


def test_diff_modified_survives_mid_block_insertion():
    prior = (
        "Risk A is about markets being volatile and hard to predict.\n"
        "Risk B is about supply chains failing under stress.\n"
        "Risk C is about currency swings hurting our results."
    )
    curr = (
        "NEW: a risk about emerging cyber threats to our systems.\n"
        "Risk A is about markets being quite volatile and hard to predict now.\n"
        "Risk B is about our supply chains failing badly under severe stress.\n"
        "Risk C is about currency swings hurting our reported results."
    )
    d = diff_risk_factors(prior, curr)
    assert len(d.modified) == 3   # A/B/C correctly paired despite the inserted NEW
    assert len(d.added) == 1 and "cyber" in d.added[0].text
    assert len(d.removed) == 0


def test_zero_sections_supported_form_is_marked_failed():
    repo = Repo(init_db(":memory:"))
    repo.upsert_filing(Filing(accession_number="g-1", cik="1", form_type="10-Q",
                              filed_at="2024-01-01"))
    pp = Preprocessor(repo, now_fn=lambda: "t")
    garbled = "<html><body><p>garbled exhibit-only content, no items</p></body></html>"
    res = pp.preprocess_html(accession_number="g-1", cik="1", form_type="10-Q",
                             filed_at="2024-01-01", period_of_report=None, html=garbled)
    assert res.sections == []
    assert repo.get_filing("g-1").status == "failed"


def test_fts_stays_in_sync_when_section_set_changes():
    repo = _repo_with_filing(accession_number="a-2", cik="1", form_type="10-Q",
                             filed_at="2024-08-02", period_of_report="2024-06-29")
    pp = Preprocessor(repo, now_fn=lambda: "t")

    def hits(word):
        return repo.conn.execute(
            "SELECT count(*) FROM section_fts WHERE section_fts MATCH ?", (word,)
        ).fetchone()[0]

    pp.preprocess_html(accession_number="a-2", cik="1", form_type="10-Q",
                       filed_at="2024-08-02", period_of_report="2024-06-29", html=TENQ)
    assert hits("Management") >= 1
    # reprocess into a DIFFERENT, smaller section set — old mdna FTS rows must be purged
    smaller = (
        "<html><body>"
        "<p><span style='font-weight:700'>Part II. Other Information</span></p>"
        "<p><span style='font-weight:700'>Item 1. Legal Proceedings</span></p>"
        "<p>We have various legal matters pending against us.</p>"
        "</body></html>"
    )
    pp.preprocess_html(accession_number="a-2", cik="1", form_type="10-Q",
                       filed_at="2024-08-02", period_of_report="2024-06-29", html=smaller)
    assert hits("Management") == 0   # orphan-free: stale mdna rows removed
    assert hits("Legal") >= 1
    assert len(repo.list_filing_sections("a-2")) == 1


def test_risk_diff_against_prior_comparable_filing():
    repo = Repo(init_db(":memory:"))
    repo.upsert_filing(Filing(accession_number="k1", cik="1", form_type="10-K",
                              filed_at="2023-11-01", period_of_report="2023-09-30"))
    repo.upsert_filing(Filing(accession_number="k2", cik="1", form_type="10-K",
                              filed_at="2024-11-01", period_of_report="2024-09-28"))
    pp = Preprocessor(repo, now_fn=lambda: "t")
    pp.preprocess_html(accession_number="k1", cik="1", form_type="10-K",
                       filed_at="2023-11-01", period_of_report="2023-09-30", html=TENK)
    tenk2 = TENK.replace(
        "intense competition and supply chain concentration",
        "intense competition, supply chain concentration, and new cybersecurity threats",
    )
    res = pp.preprocess_html(accession_number="k2", cik="1", form_type="10-K",
                             filed_at="2024-11-01", period_of_report="2024-09-28", html=tenk2)
    assert res.risk_factor_diff is not None
    assert not res.risk_factor_diff.is_empty()
