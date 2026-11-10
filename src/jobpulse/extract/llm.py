"""Provider boundary. The runner only sees `LLMClient`; OpenAI specifics stay in this file.

Uses the shared HTTP client (retry, backoff, Retry-After, timeouts). The API key is attached
per request, scrubbed from every error, and never logged.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from jobpulse.config import ExtractionConfig, ModelConfig
from jobpulse.http import FetchError, HttpClient
from jobpulse.secrets import redact


class LLMError(Exception):
    """A call that produced no usable response (transport, auth, API error)."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class LLMRequest:
    custom_id: str
    model: ModelConfig
    system: str
    user: str
    schema: dict[str, Any]
    max_output_tokens: int


@dataclass
class LLMResponse:
    text: str | None  # None when the model refused or the output was cut off
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    latency_ms: int
    finish: str = "ok"  # ok | incomplete | refusal


@dataclass
class BatchStatus:
    batch_id: str
    status: str  # validating | in_progress | finalizing | completed | failed | expired | cancelled
    output_file_id: str | None = None
    error_file_id: str | None = None
    counts: dict[str, int] = field(default_factory=dict)


class LLMClient(Protocol):
    def complete(self, req: LLMRequest) -> LLMResponse: ...


def request_body(req: LLMRequest) -> dict[str, Any]:
    """Responses-API body with Structured Outputs (strict json_schema)."""
    return {
        "model": req.model.id,
        "input": [
            {"role": "system", "content": req.system},
            {"role": "user", "content": req.user},
        ],
        "reasoning": {"effort": req.model.reasoning_effort},
        "max_output_tokens": req.max_output_tokens,
        "store": False,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "job_extraction",
                "strict": True,
                "schema": req.schema,
            }
        },
    }


def parse_response_body(body: dict[str, Any], latency_ms: int) -> LLMResponse:
    usage = body.get("usage") or {}
    details = usage.get("input_tokens_details") or {}
    text: str | None = None
    finish = "ok"
    for item in body.get("output") or []:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if part.get("type") == "output_text":
                text = (text or "") + str(part.get("text", ""))
            elif part.get("type") == "refusal":
                finish = "refusal"
    if body.get("status") == "incomplete":
        finish, text = "incomplete", None
    if finish == "refusal":
        text = None
    return LLMResponse(
        text=text,
        input_tokens=int(usage.get("input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
        cached_tokens=int(details.get("cached_tokens", 0)),
        latency_ms=latency_ms,
        finish=finish,
    )


class OpenAIClient:
    def __init__(self, cfg: ExtractionConfig, http: HttpClient, api_key: str) -> None:
        self._cfg = cfg
        self._http = http
        self._key = api_key
        self._base = cfg.base_url.rstrip("/")

    def _call(self, method: str, path: str, **kw: Any) -> Any:
        headers = {"Authorization": f"Bearer {self._key}", **kw.pop("headers", {})}
        try:
            return self._http.request(
                method,
                f"{self._base}{path}",
                headers=headers,
                timeout=self._cfg.request_timeout_s,
                **kw,
            )
        except FetchError as e:
            raise LLMError(str(e.reason), redact(e.detail, self._key)) from None

    # --- synchronous -----------------------------------------------------------------------
    def complete(self, req: LLMRequest) -> LLMResponse:
        started = time.monotonic()
        body = self._call("POST", "/responses", json_body=request_body(req))
        return parse_response_body(body, int((time.monotonic() - started) * 1000))

    # --- batch -----------------------------------------------------------------------------
    def submit_batch(self, reqs: list[LLMRequest]) -> tuple[str, str]:
        """Upload a JSONL file and create a batch. Returns (batch_id, input_file_id)."""
        lines = [
            json.dumps(
                {
                    "custom_id": r.custom_id,
                    "method": "POST",
                    "url": "/v1/responses",
                    "body": request_body(r),
                },
                separators=(",", ":"),
            )
            for r in reqs
        ]
        content = ("\n".join(lines) + "\n").encode()
        up = self._call(
            "POST",
            "/files",
            files={"file": ("batch.jsonl", content, "application/jsonl")},
            data={"purpose": "batch"},
        )
        file_id = str(up["id"])
        batch = self._call(
            "POST",
            "/batches",
            json_body={
                "input_file_id": file_id,
                "endpoint": "/v1/responses",
                "completion_window": self._cfg.batch.completion_window,
            },
        )
        return str(batch["id"]), file_id

    def get_batch(self, batch_id: str) -> BatchStatus:
        b = self._call("GET", f"/batches/{batch_id}")
        return BatchStatus(
            batch_id=batch_id,
            status=str(b.get("status")),
            output_file_id=b.get("output_file_id"),
            error_file_id=b.get("error_file_id"),
            counts={k: int(v) for k, v in (b.get("request_counts") or {}).items()},
        )

    def batch_results(self, output_file_id: str) -> dict[str, LLMResponse | LLMError]:
        text = self._call("GET", f"/files/{output_file_id}/content", expect="text")
        out: dict[str, LLMResponse | LLMError] = {}
        for line in str(text).splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            cid = str(row.get("custom_id"))
            resp = row.get("response") or {}
            if row.get("error") or int(resp.get("status_code", 0)) != 200:
                out[cid] = LLMError("batch_item_error", f"status {resp.get('status_code')}")
            else:
                out[cid] = parse_response_body(resp.get("body") or {}, 0)
        return out
