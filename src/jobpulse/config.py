"""Config loading. Every tunable lives in config/*.yaml and is validated here."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"

EmployerSource = Literal["greenhouse", "lever", "ashby"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Employer(_Strict):
    company: str
    source: EmployerSource
    board_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    validated_at: datetime
    open_job_count: int = Field(ge=0)


class SkippedEmployer(_Strict):
    company: str
    reason: str


class EmployersFile(_Strict):
    version: int
    employers: list[Employer]
    skipped: list[SkippedEmployer] = []

    @model_validator(mode="after")
    def _unique_boards(self) -> EmployersFile:
        seen: set[tuple[str, str]] = set()
        for e in self.employers:
            key = (e.source, e.board_id)
            if key in seen:
                raise ValueError(f"duplicate employer board {key}")
            seen.add(key)
        return self


class SourceConfig(_Strict):
    source_id: str
    source_name: str
    enabled: bool
    phase: int
    supports_closure: bool
    attribution_url: str


class SourcesFile(_Strict):
    sources: list[SourceConfig]

    def by_id(self) -> dict[str, SourceConfig]:
        return {s.source_id: s for s in self.sources}


class HttpConfig(_Strict):
    timeout_s: float = Field(gt=0)
    max_attempts: int = Field(ge=1)
    backoff_initial_s: float = Field(ge=0)
    backoff_max_s: float = Field(ge=0)
    retry_after_cap_s: float = Field(ge=0)
    user_agent: str


class IngestConfig(_Strict):
    max_workers: int = Field(ge=1)
    lever_page_size: int = Field(ge=1, le=1000)
    max_pages: int = Field(ge=1)
    greenhouse_include_content: bool
    ashby_include_compensation: bool
    ashby_allow_empty: bool  # False: an empty Ashby board is reported as truncated
    min_success_ratio: float = Field(ge=0, le=1)


class FreshnessConfig(_Strict):
    max_age_hours: float = Field(gt=0)
    lookback_days: int = Field(ge=1)


class StorageConfig(_Strict):
    bucket_env_var: str


class WarehouseConfig(_Strict):
    database: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    token_env_var: str
    ops_schema: str
    bronze_schema: str
    silver_schema: str
    # A posting closes after this many absences on calendar-adjacent, authoritative dates.
    closure_consecutive_absences: int = Field(ge=1)


class PipelineConfig(_Strict):
    http: HttpConfig
    ingest: IngestConfig
    freshness: FreshnessConfig
    storage: StorageConfig
    warehouse: WarehouseConfig


def _load_yaml(path: Path) -> object:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_employers(path: Path | None = None) -> EmployersFile:
    return EmployersFile.model_validate(_load_yaml(path or CONFIG_DIR / "employers.yaml"))


def load_sources(path: Path | None = None) -> SourcesFile:
    return SourcesFile.model_validate(_load_yaml(path or CONFIG_DIR / "sources.yaml"))


def load_pipeline(path: Path | None = None) -> PipelineConfig:
    return PipelineConfig.model_validate(_load_yaml(path or CONFIG_DIR / "pipeline.yaml"))


# --- extraction (P3) -------------------------------------------------------------------------


class ModelConfig(_Strict):
    id: str
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"]
    input_usd_per_mtok: float = Field(ge=0)
    cached_input_usd_per_mtok: float = Field(ge=0)
    output_usd_per_mtok: float = Field(ge=0)


class BatchConfig(_Strict):
    discount: float = Field(gt=0, le=1)
    completion_window: Literal["24h"]


class BudgetConfig(_Strict):
    run_cap_usd: float = Field(ge=0)
    backfill_cap_usd: float = Field(ge=0)


class CostModelConfig(_Strict):
    chars_per_token: float = Field(gt=0)
    prompt_overhead_tokens: int = Field(ge=0)
    expected_output_tokens: int = Field(ge=1)
    max_output_tokens: int = Field(ge=1)
    cap_safety_factor: float = Field(ge=1)
    escalation_rate: float = Field(ge=0, le=1)


class TitleFamily(_Strict):
    """One title rule. Several rules may share a name (they map to the same family). `exclude`
    applies to this rule only, so a generic-title exclusion cannot remove other families."""

    name: str = Field(pattern=r"^[a-z_]+$")
    pattern: str
    exclude: str = ""

    @model_validator(mode="after")
    def _compiles(self) -> TitleFamily:
        re.compile(self.pattern)
        if self.exclude:
            re.compile(self.exclude)
        if "'" in self.pattern or "'" in self.exclude:
            raise ValueError("title patterns may not contain a single quote")
        return self


class TitleFilterConfig(_Strict):
    exclude: str = ""
    families: list[TitleFamily] = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> TitleFilterConfig:
        if self.exclude:
            re.compile(self.exclude)
            if "'" in self.exclude:
                raise ValueError("title exclude may not contain a single quote")
        return self


class SampleConfig(_Strict):
    size: int = Field(ge=1)
    seed: int


class ExtractionConfig(_Strict):
    provider: Literal["openai"]
    api_key_env_var: str
    base_url: str
    request_timeout_s: float = Field(gt=0)
    max_workers: int = Field(ge=1)
    extract_schema: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    schema_version: int = Field(ge=1)
    max_description_chars: int = Field(ge=100)
    first_pass: ModelConfig
    escalation: ModelConfig
    batch: BatchConfig
    budget: BudgetConfig
    cost_model: CostModelConfig
    title_filter: TitleFilterConfig
    sample: SampleConfig


def load_extraction(path: Path | None = None) -> ExtractionConfig:
    return ExtractionConfig.model_validate(_load_yaml(path or CONFIG_DIR / "extraction.yaml"))
