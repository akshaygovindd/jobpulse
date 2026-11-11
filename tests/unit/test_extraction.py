"""P3 tests: title filter, cheap-first extraction, escalation, quarantine, cache, cap + queue,
Batch API, and secret handling. No network: a scripted fake client and httpx.MockTransport."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from jobpulse.config import ExtractionConfig, HttpConfig
from jobpulse.extract.llm import LLMError, LLMRequest, LLMResponse, OpenAIClient
from jobpulse.extract.runner import ExtractionError, collect_batch, run_extraction, submit_batch
from jobpulse.extract.schema import (
    Extraction,
    parse_and_check,
    strict_json_schema,
)
from jobpulse.extract.store import Store
from jobpulse.http import HttpClient
from jobpulse.secrets import load_env_keys, redact
from tests.conftest import TickingClock

D1 = date(2026, 11, 10)
DESC = (
    "We build pipelines with SQL and dbt. Salary: $120,000 - $150,000 per year. "
    "We do not sponsor visas for this role. Hybrid in NYC."
)
TITLES = {
    "1": "Senior Data Engineer",
    "2": "Data Analyst",
    "3": "Account Executive, Data Cloud",  # excluded
    "4": "Machine Learning Engineer",
    "5": "Office Manager",  # no family
    "6": "Solutions Engineer, Data",  # excluded
}


def valid_json(**over: Any) -> str:
    base: dict[str, Any] = {
        "skills": ["SQL", "dbt"],
        "seniority": "senior",
        "role_family": "data_engineer",
        "salary_min": 120000,
        "salary_max": 150000,
        "salary_currency": "USD",
        "salary_period": "year",
        "salary_original_text": "$120,000 - $150,000 per year",
        "remote_policy": "hybrid",
        "sponsorship_statement": "not_offered",
        "sponsorship_original_text": "We do not sponsor visas for this role",
        "confidence": 0.9,
    }
    base.update(over)
    return json.dumps(base)


def resp(text: str | None, inp: int = 2000, out: int = 400) -> LLMResponse:
    return LLMResponse(
        text=text,
        input_tokens=inp,
        output_tokens=out,
        cached_tokens=0,
        latency_ms=5,
        finish="ok" if text else "incomplete",
    )


class FakeClient:
    """Returns scripted responses per model id; records every call."""

    def __init__(self, script: dict[str, list[LLMResponse | LLMError]]) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[tuple[str, str]] = []

    def complete(self, req: LLMRequest) -> LLMResponse:
        self.calls.append((req.model.id, req.custom_id))
        queue = self.script[req.model.id]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, LLMError):
            raise item
        return item


def clock() -> Callable[[], datetime]:
    return TickingClock(datetime(2026, 11, 11, 9, 0, tzinfo=UTC))


def go(store: Store, ecfg: ExtractionConfig, client: Any, cap: float = 1.0, **kw: Any) -> Any:
    return run_extraction(store, ecfg, client, attempt_id="t1", cap_usd=cap, clock=clock(), **kw)


def count(store: Store, table: str) -> int:
    row = store.con.execute(f"SELECT count(*) FROM {store.t(table)}").fetchone()
    assert row
    return int(row[0])


# --- title filter ---------------------------------------------------------------------------
def test_title_filter_keeps_target_families_only(store: Store) -> None:
    active, cand = store.candidates_count()
    assert (active, cand) == (6, 3)
    titles = {
        r[0]
        for r in store.con.execute("SELECT title FROM silver.int_extraction_candidates").fetchall()
    }
    assert titles == {"Senior Data Engineer", "Data Analyst", "Machine Learning Engineer"}


# --- happy path + cache ---------------------------------------------------------------------
def test_second_run_makes_zero_calls(store: Store, ecfg: ExtractionConfig) -> None:
    luna = ecfg.first_pass.id
    first = FakeClient({luna: [resp(valid_json())]})
    s1 = go(store, ecfg, first)
    assert (s1.extracted, s1.quarantined, s1.llm_calls) == (3, 0, 3)
    assert count(store, "extractions") == 3
    assert count(store, "llm_calls") == 3
    second = FakeClient({luna: [resp(valid_json())]})
    s2 = go(store, ecfg, second)
    assert second.calls == []
    assert s2.llm_calls == 0
    assert s2.cache_hits == 3
    assert s2.pending == 0


def test_prompt_or_schema_change_misses_cache(store: Store, ecfg: ExtractionConfig) -> None:
    go(store, ecfg, FakeClient({ecfg.first_pass.id: [resp(valid_json())]}))
    store.prompt_version = "other-version"
    again = FakeClient({ecfg.first_pass.id: [resp(valid_json())]})
    go(store, ecfg, again)
    assert len(again.calls) == 3


# --- invalid -> escalation -> quarantine ------------------------------------------------------
def test_invalid_output_escalates_then_succeeds(store: Store, ecfg: ExtractionConfig) -> None:
    luna, terra = ecfg.first_pass.id, ecfg.escalation.id
    client = FakeClient({luna: [resp("{not json")], terra: [resp(valid_json())]})
    s = go(store, ecfg, client, limit=1)
    assert (s.extracted, s.escalated, s.quarantined) == (1, 1, 0)
    assert [m for m, _ in client.calls] == [luna, terra]
    outcomes = store.con.execute(
        f"SELECT model, outcome FROM {store.t('llm_calls')} ORDER BY model"
    ).fetchall()
    assert sorted(outcomes) == sorted([(luna, "escalated"), (terra, "ok")])
    esc = store.con.execute(f"SELECT escalated, model FROM {store.t('extractions')}").fetchone()
    assert esc == (True, terra)


def test_invalid_after_escalation_is_quarantined_and_not_retried(
    store: Store, ecfg: ExtractionConfig
) -> None:
    luna, terra = ecfg.first_pass.id, ecfg.escalation.id
    bad = valid_json(sponsorship_original_text="we proudly sponsor everyone")  # not in the text
    client = FakeClient({luna: [resp(bad)], terra: [resp(bad)]})
    s = go(store, ecfg, client, limit=1)
    assert (s.extracted, s.quarantined) == (0, 1)
    q = store.con.execute(
        f"SELECT model_chain, attempts, raw_output, validation_errors FROM {store.t('quarantine')}"
    ).fetchone()
    assert q is not None
    assert q[0] == f"{luna}>{terra}"
    assert q[1] == 2
    assert "proudly" in q[2]
    assert "not found in posting text" in q[3]
    # a later run does not spend on the quarantined posting again
    again = FakeClient({luna: [resp(valid_json())]})
    go(store, ecfg, again, limit=1)
    assert all(h != client.calls[0][1] for _, h in again.calls)


def test_transport_error_is_isolated_and_not_quarantined(
    store: Store, ecfg: ExtractionConfig
) -> None:
    luna = ecfg.first_pass.id
    client = FakeClient({luna: [LLMError("timeout", "slow")]})
    s = go(store, ecfg, client)
    assert (s.errors, s.extracted, s.quarantined) == (3, 0, 0)
    assert count(store, "quarantine") == 0
    assert count(store, "extractions") == 0


def test_truncated_output_counts_as_invalid(store: Store, ecfg: ExtractionConfig) -> None:
    luna, terra = ecfg.first_pass.id, ecfg.escalation.id
    client = FakeClient({luna: [resp(None)], terra: [resp(valid_json())]})
    s = go(store, ecfg, client, limit=1)
    assert s.escalated == 1
    assert s.extracted == 1


# --- spend cap + queue ------------------------------------------------------------------------
def test_cap_stops_run_and_queues_rest_without_losing_raw(
    store: Store, ecfg: ExtractionConfig
) -> None:
    before = {
        t: store.con.execute(f"SELECT count(*) FROM {t}").fetchone()
        for t in ("bronze.raw_postings", "silver.dim_postings")
    }
    client = FakeClient({ecfg.first_pass.id: [resp(valid_json())]})
    s = go(store, ecfg, client, cap=0.0001)  # below one call's worst case
    assert client.calls == []
    assert s.cap_hit
    assert s.queued == 3
    assert s.extracted == 0
    rows = store.con.execute(f"SELECT DISTINCT reason FROM {store.t('queue')}").fetchall()
    assert rows == [("budget_exhausted",)]
    after = {
        t: store.con.execute(f"SELECT count(*) FROM {t}").fetchone()
        for t in ("bronze.raw_postings", "silver.dim_postings")
    }
    assert after == before


def test_cap_never_exceeded_and_queue_drains_next_run(store: Store, ecfg: ExtractionConfig) -> None:
    from jobpulse.extract.cost import worst_case_cost

    one_call = worst_case_cost(ecfg.first_pass, 400, ecfg)
    cap = one_call * 1.2  # room for the first reservation only until it settles
    client = FakeClient({ecfg.first_pass.id: [resp(valid_json(), inp=700, out=300)]})
    s = go(store, ecfg, client, cap=cap)
    assert s.cost_usd <= cap
    assert s.extracted >= 1
    assert s.queued >= 1
    assert s.extracted + s.queued == 3
    assert s.cap_hit
    # next run with budget finishes the queue and clears it
    go(store, ecfg, FakeClient({ecfg.first_pass.id: [resp(valid_json())]}))
    assert count(store, "queue") == 0
    assert count(store, "extractions") == 3


# --- grounding and schema ---------------------------------------------------------------------
def test_grounding_rejects_quote_not_in_posting() -> None:
    ex, errs = parse_and_check(valid_json(), DESC)
    assert ex is not None
    assert not errs
    ex, errs = parse_and_check(valid_json(salary_original_text="$999 per hour"), DESC)
    assert ex is None
    assert "salary_original_text not found in posting text" in errs


@pytest.mark.parametrize(
    "over",
    [
        {"salary_min": 200000},  # min > max
        {"seniority": "wizard"},
        {"sponsorship_statement": "offered", "sponsorship_original_text": None},
        {"sponsorship_statement": "unspecified"},  # quote given but unspecified
        {"salary_original_text": None},
        {"confidence": 1.5},
    ],
)
def test_structural_validation_rejects(over: dict[str, Any]) -> None:
    ex, errs = parse_and_check(valid_json(**over), DESC)
    assert ex is None
    assert errs


def test_strict_schema_requires_every_field_and_forbids_extras() -> None:
    schema = strict_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(Extraction.model_fields)


# --- Batch API --------------------------------------------------------------------------------
KEY = "sk-test-SECRETSECRETSECRET"


def batch_client(
    ecfg: ExtractionConfig, http_cfg: HttpConfig, handler: Callable[[httpx.Request], httpx.Response]
) -> OpenAIClient:
    http = HttpClient(http_cfg, transport=httpx.MockTransport(handler), sleep=lambda _: None)
    return OpenAIClient(ecfg, http, KEY)


def make_batch_handler(
    state: dict[str, Any], outputs: Callable[[], str]
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        path = request.url.path
        if path.endswith("/files") and request.method == "POST":
            state["uploaded"] = request.content.decode()
            return httpx.Response(200, json={"id": "file-in"})
        if path.endswith("/batches") and request.method == "POST":
            state["created"] = state.get("created", 0) + 1
            return httpx.Response(200, json={"id": "batch_1", "status": "validating"})
        if path.endswith("/batches/batch_1"):
            return httpx.Response(
                200,
                json={"id": "batch_1", "status": state["status"], "output_file_id": "file-out"},
            )
        if path.endswith("/files/file-out/content"):
            return httpx.Response(200, text=outputs())
        return httpx.Response(404)

    return handler


def test_batch_submit_collect_is_idempotent_and_escalates(
    store: Store, ecfg: ExtractionConfig, http_cfg: HttpConfig
) -> None:
    state: dict[str, Any] = {"status": "in_progress"}
    hashes: list[str] = []

    def outputs() -> str:
        lines = []
        for i, h in enumerate(hashes):
            text = "oops" if i == 0 else valid_json()  # first one is invalid -> escalates
            body = {
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
                "usage": {"input_tokens": 2000, "output_tokens": 400},
            }
            lines.append(
                json.dumps(
                    {"custom_id": h, "response": {"status_code": 200, "body": body}, "error": None}
                )
            )
        return "\n".join(lines)

    client = batch_client(ecfg, http_cfg, make_batch_handler(state, outputs))
    info = submit_batch(store, ecfg, client, attempt_id="b1", cap_usd=1.0, clock=clock())
    assert info["submitted"] == 3
    assert info["batch_id"] == "batch_1"
    uploaded = [json.loads(line) for line in state["uploaded"].splitlines() if line.startswith("{")]
    assert {u["url"] for u in uploaded} == {"/v1/responses"}
    assert uploaded[0]["body"]["text"]["format"]["strict"] is True
    assert uploaded[0]["body"]["model"] == ecfg.first_pass.id
    hashes.extend(u["custom_id"] for u in uploaded)

    with pytest.raises(ExtractionError):  # an uncollected batch blocks a second submit
        submit_batch(store, ecfg, client, attempt_id="b2", cap_usd=1.0, clock=clock())
    assert state["created"] == 1

    # not finished yet: nothing collected
    stats, _ = collect_batch(store, ecfg, client, attempt_id="b3", cap_usd=1.0, clock=clock())
    assert stats.extracted == 0
    assert count(store, "extractions") == 0

    state["status"] = "completed"
    escalator = FakeClient({ecfg.escalation.id: [resp(valid_json())]})
    client.complete = escalator.complete  # type: ignore[method-assign]
    stats, _ = collect_batch(store, ecfg, client, attempt_id="b4", cap_usd=1.0, clock=clock())
    assert (stats.extracted, stats.escalated) == (3, 1)
    modes = {r[0] for r in store.con.execute(f"SELECT mode FROM {store.t('llm_calls')}").fetchall()}
    assert modes == {"batch", "sync"}
    # collecting again is a no-op
    stats, _ = collect_batch(store, ecfg, client, attempt_id="b5", cap_usd=1.0, clock=clock())
    assert stats.extracted == 0
    assert count(store, "extractions") == 3
    # and a following extract run makes no calls
    again = FakeClient({ecfg.first_pass.id: [resp(valid_json())]})
    go(store, ecfg, again)
    assert again.calls == []


def test_batch_cost_uses_discount(
    store: Store, ecfg: ExtractionConfig, http_cfg: HttpConfig
) -> None:
    from jobpulse.extract.cost import call_cost

    full = call_cost(ecfg.first_pass, 2000, 400)
    half = call_cost(ecfg.first_pass, 2000, 400, batch_discount=ecfg.batch.discount)
    assert half == pytest.approx(full * 0.5)


# --- secrets ----------------------------------------------------------------------------------
def test_api_error_text_never_contains_the_key(
    ecfg: ExtractionConfig, http_cfg: HttpConfig
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text=f"bad request for key {KEY}")

    client = batch_client(ecfg, http_cfg, handler)
    req = LLMRequest("x", ecfg.first_pass, "s", "u", {}, 10)
    with pytest.raises(LLMError) as ei:
        client.complete(req)
    assert KEY not in str(ei.value)
    assert "SECRETSECRET" not in str(ei.value)


def test_load_env_keys_reads_only_named_keys_and_never_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# comment\nOTHER=should-not-load\n"
        "export OPENAI_API_KEY='sk-abc12345678'\nPRESET=fromfile\n"
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OTHER", raising=False)
    monkeypatch.setenv("PRESET", "fromenv")
    loaded = load_env_keys(["OPENAI_API_KEY", "PRESET"], env)
    import os

    assert loaded == ["OPENAI_API_KEY"]
    assert os.environ["OPENAI_API_KEY"] == "sk-abc12345678"
    assert os.environ["PRESET"] == "fromenv"
    assert "OTHER" not in os.environ
    monkeypatch.delenv("OPENAI_API_KEY")


def test_redact_removes_key_shapes() -> None:
    assert "sk-proj-abcdef123456" not in redact("auth failed for sk-proj-abcdef123456")
    assert "mysecret" not in redact("token mysecret here", "mysecret")
