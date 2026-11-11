"""Automated (no human label) evaluations: salary rules + cross-check, grounding, agreement."""

from __future__ import annotations

import importlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from jobpulse.config import REPO_ROOT, load_pipeline
from jobpulse.extract.llm import LLMError
from jobpulse.extract.schema import quote_in_text
from tests.unit.test_extraction import FakeClient, go, resp, valid_json

sys.path.insert(0, str(REPO_ROOT / "eval"))
salary_rules = importlib.import_module("salary_rules")
auto_common = importlib.import_module("auto_common")
agreement = importlib.import_module("agreement")
salary_crosscheck = importlib.import_module("salary_crosscheck")
grounding_report = importlib.import_module("grounding_report")

TOL = 0.005


# --- salary rules -----------------------------------------------------------------------------
def pay(text: str) -> list[tuple[float, float, str | None]]:
    return [(r.low, r.high, r.period or r.inferred_period) for r in salary_rules.parse_pay(text)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Base salary range: $120,000 - $150,000 per year", [(120000, 150000, "year")]),
        ("Compensation of $55 - $65 per hour", [(55, 65, "hour")]),
        ("Pay range: USD 120K to 150K annually", [(120000, 150000, "year")]),
        ("Salary range is $150,000 \u2013 $190,000", [(150000, 190000, "year")]),  # en dash
        ("Earn $45/hr", [(45, 45, "hour")]),
        ("We raised $50M - $100M", []),  # company size, not pay
        ("A sign-on bonus of $10,000 - $20,000", []),  # no pay context, no period
        ("Stipend of $10,000 annual", []),  # implausible annual salary
        ("We offer great benefits and a collaborative culture.", []),
    ],
)
def test_parse_pay(text: str, expected: list[tuple[float, float, str]]) -> None:
    assert pay(text) == expected


def test_parse_pay_keeps_every_location_tier() -> None:
    text = "Salary range: $150,000 - $190,000. Other US locations: $135,000 - $170,000."
    assert pay(text) == [(150000, 190000, "year"), (135000, 170000, "year")]


def test_compare_classes() -> None:
    tiers = salary_rules.parse_pay("Salary: $150,000 - $190,000. Elsewhere: $135,000 - $170,000.")
    single = salary_rules.parse_pay("Pay range $100,000 - $120,000 per year")
    cmp = salary_rules.compare
    assert cmp(single, 100000, 120000, "year", TOL) == "match"
    assert cmp(single, 100000, 120000, None, TOL) == "match"
    assert cmp(single, 100000, None, None, TOL) == "match"  # a missing model bound is allowed
    assert cmp(single, 100000, 120000, "hour", TOL) == "match_period_conflict"
    assert cmp(single, 100000, 999999, None, TOL) == "partial"
    assert cmp(single, 5, 6, None, TOL) == "mismatch"
    assert cmp(single, None, None, None, TOL) == "model_missed"
    assert cmp([], 5, 6, None, TOL) == "model_only"
    assert cmp([], None, None, None, TOL) == "no_salary_both"
    assert cmp(tiers, 135000, 190000, "year", TOL) == "match_envelope"
    assert cmp(tiers, 135000, 170000, "year", TOL) == "match"  # equals one tier


# --- grounding --------------------------------------------------------------------------------
def test_quote_in_text_ignores_case_and_whitespace() -> None:
    assert quote_in_text("we  do not\nsponsor", "Note: We do not sponsor visas")
    assert not quote_in_text("we sponsor visas", "We do not sponsor visas")


# --- end to end on a fixture warehouse ----------------------------------------------------------
def make_ctx(store: Any, ecfg: Any) -> Any:
    return auto_common.Ctx(store.con, load_pipeline(), ecfg, store.prompt_version)


def test_salary_crosscheck_and_grounding_reports(
    store: Any, ecfg: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    go(store, ecfg, FakeClient({ecfg.first_pass.id: [resp(valid_json())]}))  # 3 extractions
    ctx = make_ctx(store, ecfg)
    monkeypatch.setattr(salary_crosscheck, "open_ctx", lambda _db: ctx)
    monkeypatch.setattr(grounding_report, "open_ctx", lambda _db: ctx)
    monkeypatch.setattr(sys, "argv", ["x", "--out-dir", str(tmp_path)])
    assert salary_crosscheck.main() == 0
    text = (tmp_path / "salary_crosscheck_report.md").read_text()
    assert "agreement, not accuracy" in text
    assert "| match | 3 |" in text  # fixture text says $120,000 - $150,000 per year; model agrees
    assert (tmp_path / "salary_crosscheck_disagreements.csv").exists()

    assert grounding_report.main() == 0
    g = (tmp_path / "grounding_report.md").read_text()
    assert "not accuracy" in g
    assert "100.0% (3/3)" in g  # salary quotes found
    assert "100.0% (3/3)" in g.split("sponsorship_original_text")[1]  # sponsorship quotes found


# --- agreement --------------------------------------------------------------------------------
def tiny(ecfg: Any, size: int = 3, cap: float = 0.40) -> Any:
    ag = ecfg.eval.agreement.model_copy(update={"size": size, "cap_usd": cap})
    return ecfg.model_copy(update={"eval": ecfg.eval.model_copy(update={"agreement": ag})})


def clock() -> datetime:
    return datetime(2026, 11, 12, tzinfo=UTC)


def test_agreement_sample_is_seeded_and_excludes_escalated_rows(store: Any, ecfg: Any) -> None:
    ecfg = tiny(ecfg, size=2)
    luna, terra = ecfg.first_pass.id, ecfg.escalation.id
    go(
        store,
        ecfg,
        FakeClient({luna: [resp("{bad"), resp(valid_json())], terra: [resp(valid_json())]}),
        limit=1,
    )
    go(store, ecfg, FakeClient({luna: [resp(valid_json())]}))
    ctx = make_ctx(store, ecfg)
    escalated = {
        r[0]
        for r in store.con.execute(
            f"SELECT content_hash FROM {store.t('extractions')} WHERE escalated"
        ).fetchall()
    }
    assert len(escalated) == 1
    first = agreement.sample_postings(ctx, store)
    assert len(first) == 2
    assert {p.content_hash for p in first}.isdisjoint(escalated)  # never compare terra with itself
    assert [p.content_hash for p in first] == [
        p.content_hash for p in agreement.sample_postings(ctx, store)
    ]


def test_agreement_run_is_idempotent_logged_as_eval_and_not_accuracy(
    store: Any, ecfg: Any, tmp_path: Path
) -> None:
    ecfg = tiny(ecfg)
    luna, terra = ecfg.first_pass.id, ecfg.escalation.id
    go(store, ecfg, FakeClient({luna: [resp(valid_json())]}))
    ctx = make_ctx(store, ecfg)
    postings = agreement.sample_postings(ctx, store)
    assert len(postings) == 3
    differ = valid_json(seniority="staff", skills=["SQL", "Airflow"])
    client = FakeClient({terra: [resp(valid_json()), resp(differ), resp(valid_json())]})
    stats = agreement.run_agreement(store, ctx, client, postings, clock)
    assert (stats["ok"], stats["invalid"], stats["error"], stats["not_run_cap"]) == (3, 0, 0, 0)
    assert len(client.calls) == 3
    modes = store.con.execute(
        f"SELECT DISTINCT mode FROM {store.t('llm_calls')} WHERE model = ?", [terra]
    ).fetchall()
    assert modes == [("eval",)]
    # the extraction cache and quarantine are untouched
    assert store.con.execute(f"SELECT count(*) FROM {store.t('extractions')}").fetchone() == (3,)
    # second run: nothing left to do, no calls
    again = FakeClient({terra: [resp(valid_json())]})
    stats2 = agreement.run_agreement(store, ctx, again, postings, clock)
    assert again.calls == []
    assert stats2["skipped_done"] == 3

    text = agreement.write_report(ctx, store, stats, tmp_path)
    assert "NOT accuracy" in text
    assert "Postings compared: **3**" in text
    # one of three differs on seniority (exact), but all agree once staff is folded into senior
    assert "| seniority (exact) | 66.7% (2/3) |" in text
    assert (
        "| seniority (staff/principal/manager/director/executive = senior) | 100.0% (3/3) |" in text
    )
    assert "mean Jaccard overlap" in text


def test_agreement_respects_the_cap_including_earlier_spend(store: Any, ecfg: Any) -> None:
    from jobpulse.extract.cost import worst_case_cost

    luna, terra = ecfg.first_pass.id, ecfg.escalation.id
    go(store, ecfg, FakeClient({luna: [resp(valid_json())]}))
    probe = agreement.sample_postings(make_ctx(store, tiny(ecfg)), store)
    worst = max(
        worst_case_cost(ecfg.escalation, len(p.title) + len(p.description_text), ecfg)
        for p in probe
    )
    # room for exactly one worst-case reservation: after the first call settles at its (small)
    # actual cost, spent + a second worst case no longer fits
    tight = tiny(ecfg, cap=worst * 1.1)
    ctx = make_ctx(store, tight)
    postings = agreement.sample_postings(ctx, store)
    client = FakeClient({terra: [resp(valid_json(), inp=1500, out=250)]})
    stats = agreement.run_agreement(store, ctx, client, postings, clock)
    assert stats["ok"] == 1
    assert stats["not_run_cap"] == 2
    assert stats["cost_usd"] <= tight.eval.agreement.cap_usd
    # earlier spend counts toward the same cap on a later run
    assert store.eval_spend(terra) == pytest.approx(stats["cost_usd"])
    more = FakeClient({terra: [resp(valid_json())]})
    stats2 = agreement.run_agreement(store, ctx, more, postings, clock)
    assert stats2["not_run_cap"] == 2
    assert more.calls == []


def test_agreement_api_errors_and_invalid_output_are_isolated(store: Any, ecfg: Any) -> None:
    ecfg = tiny(ecfg)
    luna, terra = ecfg.first_pass.id, ecfg.escalation.id
    go(store, ecfg, FakeClient({luna: [resp(valid_json())]}))
    ctx = make_ctx(store, ecfg)
    postings = agreement.sample_postings(ctx, store)
    client = FakeClient(
        {terra: [LLMError("timeout", "slow"), resp("{not json"), resp(valid_json())]}
    )
    stats = agreement.run_agreement(store, ctx, client, postings, clock)
    assert (stats["error"], stats["invalid"], stats["ok"]) == (1, 1, 1)
    assert store.con.execute(f"SELECT count(*) FROM {store.t('agreement')}").fetchone() == (1,)


def test_compare_fields_counts_null_agreement_separately() -> None:
    a = {
        "seniority": "senior",
        "role_family": "bi",
        "remote_policy": "remote",
        "salary_currency": None,
        "salary_period": None,
        "salary_min": None,
        "salary_max": None,
        "sponsorship_statement": "unspecified",
    }
    b = dict(a, remote_policy="hybrid")
    out = agreement.compare_fields([(a, a), (a, b)], {"staff": "senior"}, TOL)
    assert out["remote_policy"] == {"n": 2, "agree": 1, "either": 2, "agree_either": 1}
    assert out["salary_min"] == {"n": 2, "agree": 2, "either": 0, "agree_either": 0}  # null == null
    assert out["sponsorship_statement"]["either"] == 0  # unspecified is treated as not stated
