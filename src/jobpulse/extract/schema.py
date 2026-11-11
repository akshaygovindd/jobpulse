"""Extraction schema: the contract between the LLM and the warehouse.

Structural rules live in the Pydantic model. Grounding rules (quoted text must appear in the
posting) need the posting itself, so they live in `grounding_errors`. Either kind of failure
counts as "invalid" and triggers escalation.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from jobpulse.config import CONFIG_DIR

Seniority = Literal[
    "intern",
    "junior",
    "mid",
    "senior",
    "staff",
    "principal",
    "manager",
    "director",
    "executive",
    "unspecified",
]
RoleFamily = Literal[
    "data_analyst",
    "data_engineer",
    "analytics_engineer",
    "bi",
    "business_analyst",
    "data_scientist",
    "ml_ai_engineer",
    "other",
]
RemotePolicy = Literal["remote", "hybrid", "onsite", "unspecified"]
SalaryPeriod = Literal["hour", "day", "week", "month", "year"]
Sponsorship = Literal["offered", "not_offered", "unspecified"]


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    skills: list[str] = Field(max_length=40)
    seniority: Seniority
    role_family: RoleFamily
    salary_min: float | None = Field(ge=0)
    salary_max: float | None = Field(ge=0)
    salary_currency: str | None = Field(pattern=r"^[A-Z]{3}$")
    salary_period: SalaryPeriod | None
    salary_original_text: str | None
    remote_policy: RemotePolicy
    sponsorship_statement: Sponsorship
    sponsorship_original_text: str | None
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _consistent(self) -> Extraction:
        lo, hi = self.salary_min, self.salary_max
        if lo is not None and hi is not None and lo > hi:
            raise ValueError("salary_min > salary_max")
        if (lo is not None or hi is not None) and not self.salary_original_text:
            raise ValueError("salary given without salary_original_text")
        if lo is None and hi is None and self.salary_original_text:
            raise ValueError("salary_original_text without a salary")
        if self.sponsorship_statement == "unspecified" and self.sponsorship_original_text:
            raise ValueError("sponsorship_original_text given for an unspecified statement")
        if self.sponsorship_statement != "unspecified" and not self.sponsorship_original_text:
            raise ValueError("sponsorship statement without sponsorship_original_text")
        return self


_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip().casefold()


def quote_in_text(quote: str, description: str) -> bool:
    """True when `quote` appears in the posting text (whitespace- and case-insensitive)."""
    return _norm(quote) in _norm(description)


def grounding_errors(ex: Extraction, description: str) -> list[str]:
    """Quoted evidence must appear (whitespace/case-insensitively) in the posting text."""
    errors: list[str] = []
    for name in ("salary_original_text", "sponsorship_original_text"):
        quote = getattr(ex, name)
        if quote and not quote_in_text(quote, description):
            errors.append(f"{name} not found in posting text")
    return errors


def parse_and_check(raw: str, description: str) -> tuple[Extraction | None, list[str]]:
    """Parse model output. Returns (extraction, []) when valid, else (None, errors)."""
    try:
        ex = Extraction.model_validate_json(raw)
    except ValidationError as e:
        return None, [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()][:10]
    except ValueError as e:  # pragma: no cover - model_validate_json raises ValidationError
        return None, [str(e)[:200]]
    errors = grounding_errors(ex, description)
    return (ex, []) if not errors else (None, errors)


_UNSUPPORTED = {
    "default",
    "title",
    "minimum",
    "maximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "pattern",
    "format",
    "examples",
}


def _strictify(node: Any) -> Any:
    if isinstance(node, dict):
        # Pydantic enforces the dropped constraints; strict mode may reject them.
        out = {k: _strictify(v) for k, v in node.items() if k not in _UNSUPPORTED}
        if out.get("type") == "object" and "properties" in out:
            out["required"] = list(out["properties"])
            out["additionalProperties"] = False
        return out
    if isinstance(node, list):
        return [_strictify(v) for v in node]
    return node


def strict_json_schema() -> dict[str, Any]:
    """JSON Schema for OpenAI Structured Outputs (strict): all fields required, no extras."""
    schema: dict[str, Any] = _strictify(Extraction.model_json_schema())
    return schema


def load_prompt() -> str:
    return (CONFIG_DIR / "extraction_prompt.md").read_text(encoding="utf-8").strip()


def prompt_version(schema_version: int) -> str:
    """Cache-key component: changes whenever the prompt text or the JSON schema changes."""
    blob = json.dumps(strict_json_schema(), sort_keys=True) + load_prompt() + str(schema_version)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def user_message(title: str, company: str, location: str | None, description: str) -> str:
    return (
        f"Title: {title}\nCompany: {company}\nLocation: {location or 'n/a'}\n\n"
        f"Posting text:\n{description}"
    )
