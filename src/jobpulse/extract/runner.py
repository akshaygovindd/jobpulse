"""Extraction runner: cheap model first, one escalation on validation failure, then quarantine.

Isolation: a failing posting never raises past the runner. Errors become a recorded outcome and
the run continues. Raw data (bronze/silver) is never modified. Once the spend cap is reached,
remaining postings are queued, not dropped.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from jobpulse.config import ExtractionConfig, ModelConfig
from jobpulse.extract.budget import Budget
from jobpulse.extract.cost import call_cost, worst_case_cost
from jobpulse.extract.llm import LLMClient, LLMError, LLMRequest, LLMResponse, OpenAIClient
from jobpulse.extract.schema import (
    Extraction,
    load_prompt,
    parse_and_check,
    strict_json_schema,
    user_message,
)
from jobpulse.extract.store import CallRecord, Posting, Store
from jobpulse.logging import get_logger

Clock = Callable[[], datetime]


class ExtractionError(Exception):
    pass


@dataclass
class _Step:
    kind: str  # ok | invalid | error | budget
    model: ModelConfig
    extraction: Extraction | None = None
    raw: str | None = None
    errors: list[str] = field(default_factory=list)
    call: CallRecord | None = None


@dataclass
class PostingResult:
    posting: Posting
    status: str  # ok | quarantine | queued | error
    extraction: Extraction | None = None
    model: str = ""
    escalated: bool = False
    raw: str | None = None
    errors: list[str] = field(default_factory=list)
    chain: list[str] = field(default_factory=list)
    calls: list[CallRecord] = field(default_factory=list)


@dataclass
class RunStats:
    active: int = 0
    candidates: int = 0
    cache_hits: int = 0
    pending: int = 0
    extracted: int = 0
    escalated: int = 0
    quarantined: int = 0
    queued: int = 0
    errors: int = 0
    llm_calls: int = 0
    cost_usd: float = 0.0
    cap_usd: float = 0.0
    cap_hit: bool = False


class Extractor:
    def __init__(
        self,
        store: Store,
        cfg: ExtractionConfig,
        client: LLMClient,
        budget: Budget,
        *,
        attempt_id: str,
        clock: Clock,
    ) -> None:
        self.store, self.cfg, self.client, self.budget = store, cfg, client, budget
        self.attempt_id, self.clock = attempt_id, clock
        self.system = load_prompt()
        self.schema = strict_json_schema()
        self.log = get_logger(component="extract", attempt_id=attempt_id)

    # --- single steps ----------------------------------------------------------------------
    def _text(self, p: Posting) -> str:
        return p.description_text[: self.cfg.max_description_chars]

    def request(self, p: Posting, model: ModelConfig) -> LLMRequest:
        return LLMRequest(
            custom_id=p.content_hash,
            model=model,
            system=self.system,
            user=user_message(p.title, p.company, p.location_raw, self._text(p)),
            schema=self.schema,
            max_output_tokens=self.cfg.cost_model.max_output_tokens,
        )

    def step_from_response(
        self, p: Posting, model: ModelConfig, resp: LLMResponse | LLMError, *, mode: str
    ) -> _Step:
        call_id = uuid.uuid4().hex
        if isinstance(resp, LLMError):
            rec = CallRecord(call_id, p.content_hash, model.id, mode, 0, 0, 0, 0.0, 0, "error")
            return _Step("error", model, errors=[f"{resp.reason}: {resp.detail}"], call=rec)
        discount = self.cfg.batch.discount if mode == "batch" else 1.0
        cost = call_cost(
            model,
            resp.input_tokens,
            resp.output_tokens,
            resp.cached_tokens,
            batch_discount=discount,
        )
        ex, errors = (None, [f"no usable output ({resp.finish})"])
        if resp.text is not None:
            ex, errors = parse_and_check(resp.text, self._text(p))
        outcome = "ok" if ex is not None else "invalid"
        rec = CallRecord(
            call_id,
            p.content_hash,
            model.id,
            mode,
            resp.input_tokens,
            resp.output_tokens,
            resp.cached_tokens,
            cost,
            resp.latency_ms,
            outcome,
        )
        return _Step(outcome, model, ex, resp.text, errors, rec)

    def call_sync(self, p: Posting, model: ModelConfig) -> _Step:
        est = worst_case_cost(model, len(p.title) + len(self._text(p)), self.cfg)
        if not self.budget.try_reserve(est):
            return _Step("budget", model)
        try:
            resp: LLMResponse | LLMError = self.client.complete(self.request(p, model))
        except LLMError as e:
            resp = e
        except Exception as e:  # isolation: unexpected provider/client failure
            resp = LLMError("unexpected_error", type(e).__name__)
        step = self.step_from_response(p, model, resp, mode="sync")
        self.budget.settle(est, step.call.cost_usd if step.call else 0.0)
        return step

    # --- one posting -----------------------------------------------------------------------
    def process(self, p: Posting, first: _Step | None = None) -> PostingResult:
        """Run the cheap-first flow. `first` carries an already-obtained first-pass step (batch)."""
        try:
            return self._process(p, first)
        except Exception as e:  # never raise past the runner
            self.log.error("posting_failed", content_hash=p.content_hash, reason=type(e).__name__)
            return PostingResult(p, "error", errors=[type(e).__name__])

    def _process(self, p: Posting, first: _Step | None) -> PostingResult:
        cheap = first or self.call_sync(p, self.cfg.first_pass)
        calls = [cheap.call] if cheap.call else []
        chain = [self.cfg.first_pass.id]
        if cheap.kind == "budget":
            return PostingResult(p, "queued", calls=calls)
        if cheap.kind == "error":
            return PostingResult(p, "error", errors=cheap.errors, calls=calls, chain=chain)
        if cheap.kind == "ok":
            assert cheap.extraction is not None
            return PostingResult(
                p, "ok", cheap.extraction, cheap.model.id, False, calls=calls, chain=chain
            )
        # Invalid first pass: escalate once.
        assert cheap.call is not None
        cheap.call.outcome = "escalated"
        strong = self.call_sync(p, self.cfg.escalation)
        if strong.call:
            calls.append(strong.call)
        chain.append(self.cfg.escalation.id)
        if strong.kind == "budget":
            return PostingResult(p, "queued", calls=calls, chain=chain[:1], errors=cheap.errors)
        if strong.kind == "error":
            return PostingResult(
                p, "error", errors=cheap.errors + strong.errors, calls=calls, chain=chain
            )
        if strong.kind == "ok":
            assert strong.extraction is not None
            return PostingResult(
                p, "ok", strong.extraction, strong.model.id, True, calls=calls, chain=chain
            )
        return PostingResult(
            p,
            "quarantine",
            raw=strong.raw or cheap.raw,
            errors=cheap.errors + strong.errors,
            chain=chain,
            calls=calls,
        )

    # --- persistence (main thread only) ----------------------------------------------------
    def persist(self, r: PostingResult, stats: RunStats) -> None:
        now = self.clock()
        self.store.save_calls(r.calls, self.attempt_id, now)
        stats.llm_calls += len(r.calls)
        stats.cost_usd += sum(c.cost_usd for c in r.calls)
        if r.status == "ok" and r.extraction is not None:
            self.store.save_extraction(r.posting, r.extraction, r.model, r.escalated, now)
            stats.extracted += 1
            stats.escalated += int(r.escalated)
        elif r.status == "quarantine":
            self.store.save_quarantine(r.posting, r.raw, r.errors, r.chain, len(r.calls), now)
            stats.quarantined += 1
            stats.escalated += 1
        elif r.status == "queued":
            self.store.enqueue([r.posting], "budget_exhausted", self.attempt_id, now)
            stats.queued += 1
        else:
            stats.errors += 1
            self.log.warning(
                "extraction_error", content_hash=r.posting.content_hash, reason="; ".join(r.errors)
            )

    def run_all(self, items: list[tuple[Posting, _Step | None]], stats: RunStats) -> None:
        """Process postings with a small worker pool; DB writes stay on this thread."""
        with ThreadPoolExecutor(max_workers=self.cfg.max_workers) as pool:
            futures = [pool.submit(self.process, p, first) for p, first in items]
            for fut in as_completed(futures):
                self.persist(fut.result(), stats)
        stats.cost_usd = round(stats.cost_usd, 6)
        stats.cap_hit = self.budget.exhausted


def run_extraction(
    store: Store,
    cfg: ExtractionConfig,
    client: LLMClient,
    *,
    attempt_id: str,
    cap_usd: float,
    clock: Clock,
    limit: int | None = None,
) -> RunStats:
    stats = RunStats(cap_usd=cap_usd)
    stats.active, stats.candidates = store.candidates_count()
    pending, stats.cache_hits = store.pending(limit)
    stats.pending = len(pending)
    ex = Extractor(store, cfg, client, Budget(cap_usd), attempt_id=attempt_id, clock=clock)
    ex.run_all([(p, None) for p in pending], stats)
    return stats


# --- Batch API (first pass only; escalations run synchronously on collect) -------------------


def submit_batch(
    store: Store,
    cfg: ExtractionConfig,
    client: OpenAIClient,
    *,
    attempt_id: str,
    cap_usd: float,
    clock: Clock,
    limit: int | None = None,
) -> dict[str, Any]:
    open_batches = store.open_batches()
    if open_batches:
        raise ExtractionError(
            f"batch {open_batches[0][0]} is not collected yet; run `extract --batch collect` first"
        )
    pending, _ = store.pending(limit)
    ex = Extractor(store, cfg, client, Budget(cap_usd), attempt_id=attempt_id, clock=clock)
    fit: list[Posting] = []
    spent = 0.0
    queued: list[Posting] = []
    for p in pending:
        est = worst_case_cost(
            cfg.first_pass,
            len(p.title) + len(ex._text(p)),
            cfg,
            batch_discount=cfg.batch.discount,
        )
        if spent + est > cap_usd:
            queued.append(p)
        else:
            spent += est
            fit.append(p)
    now = clock()
    if queued:
        store.enqueue(queued, "budget_exhausted", attempt_id, now)
    if not fit:
        return {"submitted": 0, "queued": len(queued), "batch_id": None, "worst_case_usd": 0.0}
    batch_id, file_id = client.submit_batch([ex.request(p, cfg.first_pass) for p in fit])
    store.save_batch(
        {
            "batch_id": batch_id,
            "attempt_id": attempt_id,
            "input_file_id": file_id,
            "model": cfg.first_pass.id,
            "request_count": len(fit),
            "est_cost_usd": round(spent, 6),
            "schema_version": store.schema_version,
            "prompt_version": store.prompt_version,
            "status": "submitted",
            "created_at": now,
        }
    )
    return {
        "submitted": len(fit),
        "queued": len(queued),
        "batch_id": batch_id,
        "worst_case_usd": round(spent, 4),
    }


def collect_batch(
    store: Store,
    cfg: ExtractionConfig,
    client: OpenAIClient,
    *,
    attempt_id: str,
    cap_usd: float,
    clock: Clock,
) -> tuple[RunStats, list[str]]:
    stats = RunStats(cap_usd=cap_usd)
    notes: list[str] = []
    budget = Budget(cap_usd)
    ex = Extractor(store, cfg, client, budget, attempt_id=attempt_id, clock=clock)
    for batch_id, _ in store.open_batches():
        versions = store.batch_versions(batch_id)
        if versions != (store.schema_version, store.prompt_version):
            notes.append(f"{batch_id}: schema/prompt changed since submit; not collected")
            continue
        status = client.get_batch(batch_id)
        notes.append(f"{batch_id}: {status.status} {status.counts}")
        if status.status in {"validating", "in_progress", "finalizing", "cancelling"}:
            continue
        results = client.batch_results(status.output_file_id) if status.output_file_id else {}
        done = store.done_hashes(list(results))
        postings = store.posting_by_hash([h for h in results if h not in done])
        items: list[tuple[Posting, _Step | None]] = []
        for h, resp in results.items():
            p = postings.get(h)
            if p is None:
                continue
            step = ex.step_from_response(p, cfg.first_pass, resp, mode="batch")
            if step.call:
                budget.add_spent(step.call.cost_usd)
            items.append((p, step))
        stats.pending += len(items)
        ex.run_all(items, stats)
        store.mark_batch(batch_id, status.status, clock())
    return stats, notes
