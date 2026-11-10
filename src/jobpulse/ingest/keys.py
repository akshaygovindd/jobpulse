"""Deterministic S3 object keys. Layout documented in docs/ARCHITECTURE.md."""

from __future__ import annotations

from datetime import date


def _company_day(source_id: str, board_id: str, snapshot_date: date) -> str:
    return f"source={source_id}/company={board_id}/dt={snapshot_date.isoformat()}"


def attempt_prefix(source_id: str, board_id: str, snapshot_date: date, attempt_id: str) -> str:
    return f"raw/{_company_day(source_id, board_id, snapshot_date)}/attempt={attempt_id}"


def page_key(source_id: str, board_id: str, snapshot_date: date, attempt_id: str, page: int) -> str:
    prefix = attempt_prefix(source_id, board_id, snapshot_date, attempt_id)
    return f"{prefix}/page={page:04d}.json.gz"


def manifest_key(source_id: str, board_id: str, snapshot_date: date, attempt_id: str) -> str:
    return f"{attempt_prefix(source_id, board_id, snapshot_date, attempt_id)}/_MANIFEST.json"


def pointer_key(source_id: str, board_id: str, snapshot_date: date) -> str:
    return f"snapshots/{_company_day(source_id, board_id, snapshot_date)}/_AUTHORITATIVE.json"


def ops_key(table: str, snapshot_date: date, attempt_id: str) -> str:
    return f"ops/{table}/dt={snapshot_date.isoformat()}/attempt={attempt_id}.json"


SOURCE_REGISTRY_LATEST = "ops/source_registry/latest.json"
