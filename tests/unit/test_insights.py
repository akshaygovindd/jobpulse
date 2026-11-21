"""Data-computed insight sentences: pure functions, so every sentence is checked exactly."""

from __future__ import annotations

from jobpulse import insights as i
from jobpulse.app_ui import kpi_label


def test_delta_text() -> None:
    assert i.delta_text(120, 100) == "+20 vs last week"
    assert i.delta_text(97, 100, unit="vs 3 days ago") == "-3 vs 3 days ago"
    assert i.delta_text(100, 100) == "+0 vs last week"
    assert i.delta_text(5, None) is None


def test_kpi_labels() -> None:
    assert kpi_label(7) == "vs last week"
    assert kpi_label(1) == "vs the day before"
    assert kpi_label(3) == "vs 3 days ago"


def test_company_and_city_insights_name_the_leader_with_real_shares() -> None:
    rows = [("Stripe", 160.0), ("Airbnb", 40.0)]
    assert i.company_insight(rows, 1000) == (
        "Stripe has the most open postings: 160, 16% of all 1,000."
    )
    assert i.city_insight([("Boston", 30.0), ("Paris", 10.0)], 100) == (
        "Boston leads with 30 open postings, 30% of the 100 that name a single city."
    )


def test_role_insight_uses_the_total_of_the_rows() -> None:
    assert i.role_insight([("Data Scientist", 30.0), ("Data Engineer", 10.0)]) == (
        "Data Scientist is the largest role family: 30 of 40 analyzed postings (75%)."
    )


def test_remote_insight_never_crowns_not_stated() -> None:
    rows = [("Not stated", 50.0), ("Hybrid", 30.0), ("Remote", 20.0)]
    assert i.remote_insight(rows) == (
        "Hybrid is the most common stated arrangement: 30% of analyzed postings (50% do not say)."
    )
    assert i.remote_insight([("Remote", 4.0)]) == (
        "Remote is the most common stated arrangement: 100% of analyzed postings."
    )
    assert i.remote_insight([("Not stated", 4.0)]) is None


def test_daily_flow_insight() -> None:
    rows = [("2026-11-11", 100.0), ("2026-11-12", 300.0)]
    assert i.daily_flow_insight(rows) == (
        "400 new postings across 2 days; the busiest day was 2026-11-12 with 300."
    )
    assert "closed postings" in (i.daily_flow_insight(rows, noun="closed postings") or "")


def test_skill_salary_and_sponsor_insights() -> None:
    assert i.skill_insight([("python", 82.4), ("sql", 70.0)], "Data Scientist") == (
        "For Data Scientist, the most requested skill is python: it appears in 82% of postings."
    )
    assert i.salary_insight([("Senior Data Scientist", 180.0, 9), ("Mid Analyst", 120.0, 7)]) == (
        "The highest median advertised pay is Senior Data Scientist: $180K (from 9 postings)."
    )
    text = i.sponsors_insight([("A", 90.0), ("B", 10.0)]) or ""
    assert text.startswith("A has the most certified filings for these roles: 90, 90% of all 100")
    wider = i.sponsors_insight([("A", 90.0), ("B", 10.0)], total=600.0) or ""
    assert "90, 15% of all 600" in wider  # share of every company, not of the top few shown
    assert "not that any posting is sponsored" in text  # the evidence caveat travels with it


def test_salary_insight_names_the_highest_chart_row_even_when_small() -> None:
    """Regression (QA P0-3): the sentence skipped groups under 10 salaries that the heatmap drew,
    so it named Staff $276K while the chart showed Principal $329K (5 salaries)."""
    rows = [("Staff ML/AI", 276.0, 24), ("Principal ML/AI", 328.5, 5), ("Manager X", 304.0, 6)]
    text = i.salary_insight(rows, small_below=10) or ""
    assert text.startswith("The highest median advertised pay is Principal ML/AI: $329K (from 5")
    assert "small sample" in text
    assert "small sample" not in (i.salary_insight(rows[:1], small_below=10) or "")


def test_k_text_rounds_half_up_like_the_chart() -> None:
    assert i.k_text(172.5) == "$173K"  # Python's own rounding would give $172K
    assert i.k_text(173.49) == "$173K"
    assert i.k_text(None) == "—"
    assert i.k_text(float("nan")) == "—"
    assert i.k_text(12.0, sign="CAD ") == "CAD 12K"


def test_nothing_is_said_without_data() -> None:
    assert i.company_insight([], 10) is None
    assert i.company_insight([("A", 1.0)], 0) is None
    assert i.role_insight([]) is None
    assert i.city_insight([], 5) is None
    assert i.daily_flow_insight([]) is None
    assert i.skill_insight([], "X") is None
    assert i.salary_insight([]) is None
    assert i.sponsors_insight([("A", 0.0)]) is None
    assert i.share(1, 0) == "n/a"


def test_concentration_insight() -> None:
    rows = [("A", 50.0), ("B", 30.0), ("C", 10.0), ("D", 10.0)]
    assert i.concentration_insight(rows) == (
        "The 3 busiest cities (A; B; C) hold 90% of the 100 mapped postings."
    )
    assert i.concentration_insight(rows[:2]) is None
    assert i.concentration_insight([]) is None


def test_company_card_insight() -> None:
    text = i.company_card_insight(
        [(2025, 40.0), (2026, 60.0)], ("Data Scientist", 30.0), ("Level III", 50.0)
    )
    assert text == (
        "Most certified filings came in FY2026: 60 of 100. The most common data role family is "
        "Data Scientist (30, a lower bound). The most common stated wage level is Level III (50)."
    )
    assert i.company_card_insight([(2026, 5.0)], None, None) == (
        "Most certified filings came in FY2026: 5 of 5."
    )
    assert i.company_card_insight([], None, None) is None
    assert i.company_card_insight([(2026, 0.0)], None, None) is None
