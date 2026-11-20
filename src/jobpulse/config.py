"""Config loading. Every tunable lives in config/*.yaml and is validated here."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

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
    # Dev environment (the local Airflow demo and any other dev run): every S3 key is written under
    # this prefix, which must not overlap the production key roots. See jobpulse/env.py.
    dev_prefix: str
    prod_prefixes: list[str]  # the production key roots: raw/, snapshots/, ops/


class WarehouseConfig(_Strict):
    database: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    token_env_var: str
    ops_schema: str
    bronze_schema: str
    silver_schema: str
    gold_schema: str
    ref_schema: str  # reference data loaded from public filings (LCA disclosures)
    # A posting closes after this many absences on calendar-adjacent, authoritative dates.
    closure_consecutive_absences: int = Field(ge=1)

    @property
    def gold_stage_schema(self) -> str:
        """Holds the Python-built mapping tables and int_ models, apart from what the app reads."""
        return f"{self.gold_schema}_stage"


class GoldConfig(_Strict):
    # Periods that can be annualized -> multiplier. A period absent here (e.g. "day") is never
    # annualized: the annual columns stay NULL rather than guess working days.
    annualization: dict[str, float]
    # An agg_salary_by_role_city group publishes percentiles only with at least this many salaries.
    min_salaries_for_percentiles: int = Field(ge=1)
    apply_url_hosts: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _factors_positive(self) -> GoldConfig:
        bad = [k for k, v in self.annualization.items() if v <= 0]
        if bad:
            raise ValueError(f"annualization factors must be > 0: {bad}")
        return self


class PipelineConfig(_Strict):
    http: HttpConfig
    ingest: IngestConfig
    freshness: FreshnessConfig
    storage: StorageConfig
    warehouse: WarehouseConfig
    gold: GoldConfig


class LocationsConfig(_Strict):
    """Rules for the city heuristic (src/jobpulse/location.py). No structured location exists in
    the source data, so `city` is derived from location_raw and is NULL when it is ambiguous."""

    remote_pattern: str  # a location matching this has no single city
    multi_pattern: str  # a location matching this lists several places
    noise_pattern: str  # office/HQ decorations stripped before parsing
    regions: list[str]  # lower-case states, provinces, countries: never a city, may follow one
    also_cities: list[str]  # lower-case region names that are also cities (New York, Washington)
    aliases: dict[str, str]  # lower-case spelling -> canonical city
    us_states: dict[
        str, list[str]
    ]  # canonical state -> lower-case aliases (country: United States)
    ca_provinces: dict[str, list[str]]  # canonical province -> lower-case aliases (country: Canada)
    countries: dict[str, list[str]]  # canonical country -> lower-case aliases
    country_codes: dict[str, str]  # ISO code used in "GB-London" style locations -> country
    known_places: dict[str, dict[str, str]]  # bare city -> {state: ..} or {country: ..} to fill in


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


class SponsorshipSampleConfig(_Strict):
    size: int = Field(ge=1)
    seed: int
    pattern: str

    @model_validator(mode="after")
    def _compiles(self) -> SponsorshipSampleConfig:
        re.compile(self.pattern)
        return self


class SalaryPeriodInferenceConfig(_Strict):
    year_min: float = Field(gt=0)
    hour_min: float = Field(gt=0)
    hour_max: float = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> SalaryPeriodInferenceConfig:
        if self.hour_max > self.year_min:
            raise ValueError("hour_max must not exceed year_min")
        if self.hour_min >= self.hour_max:
            raise ValueError("hour_min must be below hour_max")
        return self


class AgreementConfig(_Strict):
    size: int = Field(ge=1)
    seed: int
    cap_usd: float = Field(gt=0)
    workers: int = Field(ge=1)


class EvalConfig(_Strict):
    seniority_fold: dict[str, str]
    skills_out_of_scope_pattern: str
    agreement: AgreementConfig
    salary_tolerance: float = Field(ge=0, lt=1)

    @model_validator(mode="after")
    def _valid(self) -> EvalConfig:
        re.compile(self.skills_out_of_scope_pattern)
        return self


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
    sponsorship_sample: SponsorshipSampleConfig
    salary_period_inference: SalaryPeriodInferenceConfig
    eval: EvalConfig


def load_locations(path: Path | None = None) -> LocationsConfig:
    return LocationsConfig.model_validate(_load_yaml(path or CONFIG_DIR / "locations.yaml"))


def load_extraction(path: Path | None = None) -> ExtractionConfig:
    return ExtractionConfig.model_validate(_load_yaml(path or CONFIG_DIR / "extraction.yaml"))


class LcaMatchConfig(_Strict):
    threshold: float = Field(ge=0, le=1)  # confidence at or above which a match counts as evidence
    confidence: dict[str, float]  # method -> confidence; fuzzy suggestions are never in here
    fuzzy_suggestion_min_similarity: float = Field(gt=0, le=1)
    legal_suffixes: list[str]  # trailing words dropped by normalization (inc, llc, ...)

    @model_validator(mode="after")
    def _methods(self) -> LcaMatchConfig:
        if set(self.confidence) != {"exact", "alias", "normalized"}:
            raise ValueError("confidence must define exactly: exact, alias, normalized")
        if any(not 0 < v <= 1 for v in self.confidence.values()):
            raise ValueError("match confidences must be in (0, 1]")
        return self


class LcaReviewConfig(_Strict):
    sample_size: int = Field(ge=1)  # employer-match pairs drawn for the owner to label yes/no
    seed: int


class LcaWageConfig(_Strict):
    plausible_annual_min: float = Field(gt=0)
    plausible_annual_max: float = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> LcaWageConfig:
        if self.plausible_annual_min >= self.plausible_annual_max:
            raise ValueError("wage.plausible_annual_min must be below plausible_annual_max")
        return self


class LcaConfig(_Strict):
    """Which DOL OFLC LCA disclosure files to load and how to read and match them."""

    fiscal_years: list[int]
    visa_classes: list[str]  # VISA_CLASS values kept (exact, case-insensitive): H-1B only
    # canonical column -> accepted DOL header names (DOL renames columns between years)
    columns: dict[str, list[str]]
    certified_statuses: list[str]  # CASE_STATUS values counted as certified (upper-case)
    required: list[str]  # canonical columns that must be present, else the load fails loudly
    match: LcaMatchConfig
    wage: LcaWageConfig
    review: LcaReviewConfig

    @model_validator(mode="after")
    def _required_known(self) -> LcaConfig:
        unknown = [c for c in self.required if c not in self.columns]
        if unknown:
            raise ValueError(f"required columns without a mapping: {unknown}")
        return self


class LcaAliases(_Strict):
    """Owner-reviewed LCA employer names per company (compared after normalization)."""

    aliases: dict[str, list[str]]  # same employer under another legal name
    subsidiaries: dict[str, list[str]] = {}  # subsidiaries whose filings count for the parent
    excluded: dict[str, list[str]] = {}  # names that look like the company but are not


def load_lca(path: Path | None = None) -> LcaConfig:
    return LcaConfig.model_validate(_load_yaml(path or CONFIG_DIR / "lca.yaml"))


def load_lca_aliases(path: Path | None = None) -> LcaAliases:
    return LcaAliases.model_validate(_load_yaml(path or CONFIG_DIR / "lca_aliases.yaml"))


class ResumeUploadConfig(_Strict):
    max_bytes: int = Field(ge=1)
    max_pdf_pages: int = Field(ge=1)
    max_pdf_decoded_bytes: int = Field(ge=1)
    max_text_chars: int = Field(ge=1)
    allowed_extensions: list[str] = Field(min_length=1)


class ResumeParseConfig(_Strict):
    max_ngram: int = Field(ge=1, le=8)
    case_sensitive_skills: dict[str, str]
    ignore_skills: list[str]
    min_posting_frequency: int = Field(ge=1)
    title_keywords: list[str] = Field(min_length=1)
    education_keywords: dict[str, list[str]]  # label -> lower-case phrases that signal it
    max_title_chars: int = Field(ge=1)
    max_titles: int = Field(ge=0)
    max_years: int = Field(ge=1)


class ResumeLevelsConfig(_Strict):
    # Experience-level groups -> the gold `seniority` values they cover. A filter and a default
    # only: the level is never part of the match score.
    groups: dict[str, list[str]] = Field(min_length=1)
    entry_group: str
    mid_group: str
    senior_groups: list[str]
    entry_max_years: int = Field(ge=0)  # at most this many years (or an intern title) => entry
    senior_min_years: int = Field(ge=1)  # at least this many years => senior groups

    @model_validator(mode="after")
    def _groups_exist(self) -> ResumeLevelsConfig:
        named = [self.entry_group, self.mid_group, *self.senior_groups]
        missing = [g for g in named if g not in self.groups]
        if missing:
            raise ValueError(f"unknown experience groups: {missing}")
        return self


class ResumeMatchConfig(_Strict):
    min_resume_skills: int = Field(ge=1)  # fewer recognized skills: nothing is ranked
    top_learn_n: int = Field(ge=1)  # postings (best matches) used for "Top skills to learn"
    top_learn_max: int = Field(ge=1)  # skills listed
    min_posting_skills: int = Field(ge=1)
    min_matched: int = Field(ge=1)
    max_results: int = Field(ge=1)


class ResumeConfig(_Strict):
    upload: ResumeUploadConfig
    parse: ResumeParseConfig
    levels: ResumeLevelsConfig
    match: ResumeMatchConfig


def load_resume(path: Path | None = None) -> ResumeConfig:
    return ResumeConfig.model_validate(_load_yaml(path or CONFIG_DIR / "resume.yaml"))


class PublishReleaseConfig(_Strict):
    tag: str = Field(pattern=r"^[A-Za-z0-9._-]+$")
    asset: str = Field(pattern=r"^[A-Za-z0-9._-]+\.duckdb$")
    url_prefix: str = Field(pattern=r"^https://")

    @model_validator(mode="after")
    def _catalog_is_not_gold(self) -> PublishReleaseConfig:
        # DuckDB names a database after its file; a catalog called "gold" would make `gold.table`
        # ambiguous with the schema of the same name.
        if self.asset.removesuffix(".duckdb") == "gold":
            raise ValueError("the asset may not be called gold.duckdb")
        return self


class PublishExportConfig(_Strict):
    min_rows: dict[str, int]


class PublishAppConfig(_Strict):
    refresh_s: int = Field(ge=0)
    max_download_bytes: int = Field(ge=1)
    cache_dir_name: str = Field(pattern=r"^[A-Za-z0-9._-]+$")


class PublishConfig(_Strict):
    release: PublishReleaseConfig
    export: PublishExportConfig
    app: PublishAppConfig


def load_publish(path: Path | None = None) -> PublishConfig:
    return PublishConfig.model_validate(_load_yaml(path or CONFIG_DIR / "publish.yaml"))


class KpiConfig(_Strict):
    delta_days: int = Field(ge=1)


class MapConfig(_Strict):
    coords_file: str = Field(pattern=r"^[A-Za-z0-9._-]+\.yaml$")
    initial_lat: float = Field(ge=-90, le=90)
    initial_lon: float = Field(ge=-180, le=180)
    initial_zoom: float = Field(ge=0, le=20)
    min_radius_px: int = Field(ge=1)
    max_radius_px: int = Field(ge=1)
    attribution: str = Field(min_length=1)

    @model_validator(mode="after")
    def _radii_ordered(self) -> MapConfig:
        if self.max_radius_px < self.min_radius_px:
            raise ValueError("max_radius_px must be at least min_radius_px")
        return self


class HomeConfig(_Strict):
    top_skills: int = Field(ge=1)


class TrendsConfig(_Strict):
    min_full_weeks: int = Field(ge=1)  # "Skills over time" appears once this many full weeks exist


class SamplesConfig(_Strict):
    min_group_n: int = Field(ge=1)  # smaller groups never drive an insight sentence or a default


class AppConfig(_Strict):
    samples: SamplesConfig
    kpi: KpiConfig
    map: MapConfig
    home: HomeConfig
    trends: TrendsConfig


def load_app(path: Path | None = None) -> AppConfig:
    return AppConfig.model_validate(_load_yaml(path or CONFIG_DIR / "app.yaml"))


def load_city_coords(cfg: AppConfig | None = None) -> dict[str, tuple[float, float]]:
    """City label -> (latitude, longitude), validated. Hand-entered reference points."""
    cfg = cfg or load_app()
    raw = cast("dict[str, list[float]]", _load_yaml(CONFIG_DIR / cfg.map.coords_file))
    out: dict[str, tuple[float, float]] = {}
    for label, pair in raw.items():
        lat, lon = float(pair[0]), float(pair[1])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError(f"coordinates out of range for {label!r}")
        out[str(label)] = (lat, lon)
    return out


def load_skill_display(path: Path | None = None) -> dict[str, str]:
    """Canonical skill name -> display name (acronyms and brands). Data keeps canonical names."""
    raw = cast("dict[str, str]", _load_yaml(path or CONFIG_DIR / "skill_display.yaml"))
    return {str(k): str(v) for k, v in raw.items()}
