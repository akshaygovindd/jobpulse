"""Warehouse tables for extraction: extractions (cache), quarantine, queue, llm_calls, batches.

The extraction tables are written only by this module. Bronze and silver are read-only here:
no extraction outcome (success, quarantine, budget stop) can lose or alter raw data.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import duckdb

from jobpulse.config import ExtractionConfig, WarehouseConfig
from jobpulse.extract.schema import Extraction

_DDL = {
    "extractions": """
        content_hash VARCHAR NOT NULL, schema_version INTEGER NOT NULL,
        prompt_version VARCHAR NOT NULL, posting_key VARCHAR NOT NULL, model VARCHAR NOT NULL,
        escalated BOOLEAN NOT NULL, skills VARCHAR[], seniority VARCHAR, role_family VARCHAR,
        salary_min DOUBLE, salary_max DOUBLE, salary_currency VARCHAR, salary_period VARCHAR,
        salary_original_text VARCHAR, remote_policy VARCHAR, sponsorship_statement VARCHAR,
        sponsorship_original_text VARCHAR, confidence DOUBLE, extracted_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (content_hash, schema_version, prompt_version)
    """,
    "quarantine": """
        content_hash VARCHAR NOT NULL, schema_version INTEGER NOT NULL,
        prompt_version VARCHAR NOT NULL, posting_key VARCHAR NOT NULL, raw_output VARCHAR,
        validation_errors VARCHAR, model_chain VARCHAR NOT NULL, attempts INTEGER NOT NULL,
        quarantined_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (content_hash, schema_version, prompt_version)
    """,
    "queue": """
        posting_key VARCHAR NOT NULL, content_hash VARCHAR NOT NULL,
        enqueued_at TIMESTAMPTZ NOT NULL, reason VARCHAR NOT NULL, attempt_id VARCHAR NOT NULL,
        PRIMARY KEY (content_hash)
    """,
    "llm_calls": """
        call_id VARCHAR PRIMARY KEY, attempt_id VARCHAR NOT NULL, content_hash VARCHAR NOT NULL,
        model VARCHAR NOT NULL, mode VARCHAR NOT NULL, input_tokens INTEGER NOT NULL,
        output_tokens INTEGER NOT NULL, cached_tokens INTEGER NOT NULL,
        cost_usd DOUBLE NOT NULL, latency_ms INTEGER NOT NULL, outcome VARCHAR NOT NULL,
        created_at TIMESTAMPTZ NOT NULL
    """,
    "batches": """
        batch_id VARCHAR PRIMARY KEY, attempt_id VARCHAR NOT NULL, input_file_id VARCHAR,
        model VARCHAR NOT NULL, request_count INTEGER NOT NULL, est_cost_usd DOUBLE NOT NULL,
        schema_version INTEGER NOT NULL, prompt_version VARCHAR NOT NULL,
        status VARCHAR NOT NULL, created_at TIMESTAMPTZ NOT NULL, collected_at TIMESTAMPTZ
    """,
}


@dataclass(frozen=True)
class Posting:
    posting_key: str
    content_hash: str
    source_id: str
    company: str
    title: str
    location_raw: str | None
    description_text: str
    title_family: str


@dataclass
class CallRecord:
    call_id: str
    content_hash: str
    model: str
    mode: str  # sync | batch
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    cost_usd: float
    latency_ms: int
    outcome: str  # ok | invalid | error | escalated


class Store:
    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        wcfg: WarehouseConfig,
        ecfg: ExtractionConfig,
        prompt_version: str,
    ) -> None:
        self.con = con
        self.s = ecfg.extract_schema
        self.silver = wcfg.silver_schema
        self.schema_version = ecfg.schema_version
        self.prompt_version = prompt_version
        con.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.s}"')
        for table, cols in _DDL.items():
            con.execute(f'CREATE TABLE IF NOT EXISTS "{self.s}"."{table}" ({cols})')

    def t(self, table: str) -> str:
        return f'"{self.s}"."{table}"'

    @contextmanager
    def _tx(self) -> Iterator[None]:
        self.con.execute("BEGIN")
        try:
            yield
        except BaseException:
            self.con.execute("ROLLBACK")
            raise
        self.con.execute("COMMIT")

    # --- selection -------------------------------------------------------------------------
    def candidates_count(self) -> tuple[int, int]:
        """(active postings, candidates passing the title filter)."""
        row = self.con.execute(
            f'SELECT (SELECT count(*) FROM "{self.silver}"."dim_postings" '
            f"WHERE source_status = 'active'), "
            f'(SELECT count(*) FROM "{self.silver}"."int_extraction_candidates")'
        ).fetchone()
        assert row is not None
        return int(row[0]), int(row[1])

    def family_counts(self) -> list[tuple[str, str, int]]:
        rows = self.con.execute(
            f"SELECT title_family, source_id, count(*) "
            f'FROM "{self.silver}"."int_extraction_candidates" GROUP BY 1, 2 ORDER BY 1, 2'
        ).fetchall()
        return [(str(a), str(b), int(c)) for a, b, c in rows]

    def pending(self, limit: int | None = None) -> tuple[list[Posting], int]:
        """Candidates without a cached extraction or quarantine entry for the current versions.

        One posting per content_hash (identical content is extracted once). Returns
        (postings to process, cache hits among candidate content hashes).
        """
        params = [self.schema_version, self.prompt_version]
        hit_row = self.con.execute(
            f"""SELECT count(DISTINCT c.content_hash)
                FROM "{self.silver}"."int_extraction_candidates" c
                WHERE EXISTS (SELECT 1 FROM {self.t("extractions")} e
                              WHERE e.content_hash = c.content_hash
                                AND e.schema_version = ? AND e.prompt_version = ?)""",
            params,
        ).fetchone()
        sql = f"""
            SELECT posting_key, content_hash, source_id, company, title, location_raw,
                   description_text, title_family
            FROM (
                SELECT *, row_number() OVER (PARTITION BY content_hash ORDER BY posting_key) AS rn
                FROM "{self.silver}"."int_extraction_candidates" c
                WHERE NOT EXISTS (SELECT 1 FROM {self.t("extractions")} e
                                  WHERE e.content_hash = c.content_hash
                                    AND e.schema_version = ? AND e.prompt_version = ?)
                  AND NOT EXISTS (SELECT 1 FROM {self.t("quarantine")} q
                                  WHERE q.content_hash = c.content_hash
                                    AND q.schema_version = ? AND q.prompt_version = ?)
            ) WHERE rn = 1 ORDER BY content_hash
        """
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = self.con.execute(sql, params + params).fetchall()
        postings = [
            Posting(r[0], r[1], r[2], r[3] or r[2], r[4], r[5], r[6] or "", r[7]) for r in rows
        ]
        return postings, int(hit_row[0]) if hit_row else 0

    def posting_by_hash(self, content_hashes: Sequence[str]) -> dict[str, Posting]:
        if not content_hashes:
            return {}
        marks = ",".join("?" for _ in content_hashes)
        rows = self.con.execute(
            f"""SELECT posting_key, content_hash, source_id, company, title, location_raw,
                       description_text, title_family
                FROM "{self.silver}"."int_extraction_candidates"
                WHERE content_hash IN ({marks}) QUALIFY row_number()
                    OVER (PARTITION BY content_hash ORDER BY posting_key) = 1""",
            list(content_hashes),
        ).fetchall()
        return {
            r[1]: Posting(r[0], r[1], r[2], r[3] or r[2], r[4], r[5], r[6] or "", r[7])
            for r in rows
        }

    def done_hashes(self, content_hashes: Sequence[str]) -> set[str]:
        """Content hashes already extracted or quarantined for the current versions."""
        if not content_hashes:
            return set()
        marks = ",".join("?" for _ in content_hashes)
        rows = self.con.execute(
            f"""SELECT content_hash FROM {self.t("extractions")}
                WHERE schema_version = ? AND prompt_version = ? AND content_hash IN ({marks})
                UNION SELECT content_hash FROM {self.t("quarantine")}
                WHERE schema_version = ? AND prompt_version = ? AND content_hash IN ({marks})""",
            [
                self.schema_version,
                self.prompt_version,
                *content_hashes,
                self.schema_version,
                self.prompt_version,
                *content_hashes,
            ],
        ).fetchall()
        return {str(r[0]) for r in rows}

    # --- writes ----------------------------------------------------------------------------
    def save_calls(self, calls: Sequence[CallRecord], attempt_id: str, now: datetime) -> None:
        for c in calls:
            self.con.execute(
                f"INSERT OR REPLACE INTO {self.t('llm_calls')} VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    c.call_id,
                    attempt_id,
                    c.content_hash,
                    c.model,
                    c.mode,
                    c.input_tokens,
                    c.output_tokens,
                    c.cached_tokens,
                    c.cost_usd,
                    c.latency_ms,
                    c.outcome,
                    now,
                ],
            )

    def save_extraction(
        self, p: Posting, ex: Extraction, model: str, escalated: bool, now: datetime
    ) -> None:
        with self._tx():
            self.con.execute(
                f"DELETE FROM {self.t('queue')} WHERE content_hash = ?", [p.content_hash]
            )
            self.con.execute(
                f"INSERT OR REPLACE INTO {self.t('extractions')} "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    p.content_hash,
                    self.schema_version,
                    self.prompt_version,
                    p.posting_key,
                    model,
                    escalated,
                    ex.skills,
                    ex.seniority,
                    ex.role_family,
                    ex.salary_min,
                    ex.salary_max,
                    ex.salary_currency,
                    ex.salary_period,
                    ex.salary_original_text,
                    ex.remote_policy,
                    ex.sponsorship_statement,
                    ex.sponsorship_original_text,
                    ex.confidence,
                    now,
                ],
            )

    def save_quarantine(
        self,
        p: Posting,
        raw_output: str | None,
        errors: list[str],
        chain: list[str],
        attempts: int,
        now: datetime,
    ) -> None:
        with self._tx():
            self.con.execute(
                f"DELETE FROM {self.t('queue')} WHERE content_hash = ?", [p.content_hash]
            )
            self.con.execute(
                f"INSERT OR REPLACE INTO {self.t('quarantine')} VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    p.content_hash,
                    self.schema_version,
                    self.prompt_version,
                    p.posting_key,
                    raw_output,
                    json.dumps(errors),
                    ">".join(chain),
                    attempts,
                    now,
                ],
            )

    def enqueue(
        self, postings: Sequence[Posting], reason: str, attempt_id: str, now: datetime
    ) -> None:
        for p in postings:
            self.con.execute(
                f"INSERT OR REPLACE INTO {self.t('queue')} VALUES (?,?,?,?,?)",
                [p.posting_key, p.content_hash, now, reason, attempt_id],
            )

    def queue_size(self) -> int:
        row = self.con.execute(f"SELECT count(*) FROM {self.t('queue')}").fetchone()
        return int(row[0]) if row else 0

    # --- batches ---------------------------------------------------------------------------
    def open_batches(self) -> list[tuple[str, str]]:
        rows = self.con.execute(
            f"SELECT batch_id, status FROM {self.t('batches')} WHERE collected_at IS NULL "
            "ORDER BY created_at"
        ).fetchall()
        return [(str(a), str(b)) for a, b in rows]

    def save_batch(self, row: dict[str, Any]) -> None:
        self.con.execute(
            f"INSERT OR REPLACE INTO {self.t('batches')} VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                row["batch_id"],
                row["attempt_id"],
                row["input_file_id"],
                row["model"],
                row["request_count"],
                row["est_cost_usd"],
                row["schema_version"],
                row["prompt_version"],
                row["status"],
                row["created_at"],
                row.get("collected_at"),
            ],
        )

    def batch_versions(self, batch_id: str) -> tuple[int, str] | None:
        row = self.con.execute(
            f"SELECT schema_version, prompt_version FROM {self.t('batches')} WHERE batch_id = ?",
            [batch_id],
        ).fetchone()
        return (int(row[0]), str(row[1])) if row else None

    def mark_batch(self, batch_id: str, status: str, collected_at: datetime | None) -> None:
        self.con.execute(
            f"UPDATE {self.t('batches')} SET status = ?, collected_at = ? WHERE batch_id = ?",
            [status, collected_at, batch_id],
        )
