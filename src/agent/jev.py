"""Jev transport boundary. Only sanitized scalar routing features cross it."""

import json
from typing import Literal, Mapping

import httpx
from pydantic import Field, ValidationError

from src.agent.schemas import ContractModel, ModelRoute, RoutingFeatures


ReasonCode = Literal[
    "simple_search", "simple_followup", "structured_extraction",
    "multi_step_planning", "constraint_conflict", "comparison_reasoning",
    "temporal_reasoning", "geo_reasoning", "complex_dialogue",
    "security_risk", "low_confidence", "router_failure",
]


class JevDecision(ContractModel):
    route: ModelRoute
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)
    reason_code: ReasonCode


class JevInvalidResponse(Exception):
    """The transport succeeded but the structured response failed validation."""


_SYSTEM_PROMPT = (
    "Choose compute complexity for a Bashkortostan event assistant. "
    "Use only the supplied scalar features; there is no user text. "
    "For intent search_events with constraint_count <= 3, requires_multi_step=false, "
    "requires_comparison=false, requires_clarification=false, dialogue_depth=0, and all "
    "security flags false, always return LIGHT with reason_code simple_search, even if "
    "requires_tools is true. Never use DEFAULT as reason_code. "
    "Use HEAVY for requires_multi_step or planner (multi_step_planning), "
    "requires_comparison (comparison_reasoning), requires_clarification or "
    "dialogue_depth > 5 (complex_dialogue), or constraint_count >= 4 (constraint_conflict). "
    "Do not infer dialogue complexity when dialogue_depth is zero and requires_clarification is false. "
    "Return only JSON with route LIGHT or HEAVY, confidence as a JSON number (never a quoted "
    "string) between 0 and 1, and reason_code "
    "from this exact list: simple_search, simple_followup, structured_extraction, "
    "multi_step_planning, constraint_conflict, comparison_reasoning, temporal_reasoning, "
    "geo_reasoning, complex_dialogue, security_risk, low_confidence, router_failure. "
    "Never select tools or answer the user."
)


class JevAdapter:
    """OpenAI-compatible chat completions adapter; owns no dialogue or tools."""

    def __init__(self, endpoint: str, model: str, api_key: str = "", *,
                 timeout_seconds: float = 5, client: httpx.Client | None = None,
                 llama_schema: bool = False) -> None:
        self.endpoint = endpoint
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.client = client or httpx.Client()
        self.llama_schema = llama_schema

    def decide(self, payload: Mapping[str, str | int | bool]) -> JevDecision:
        # Reject any extra or missing key, including accidental raw text fields.
        if set(payload) != set(RoutingFeatures.model_fields):
            raise ValueError("router payload does not match the safe feature allowlist")
        safe_payload = RoutingFeatures.model_validate(payload).to_router_payload()
        if safe_payload != dict(payload):
            raise ValueError("router payload has invalid scalar values")
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        if self.endpoint.rstrip("/").endswith("/decide"):
            response = self.client.post(self.endpoint, json=safe_payload, headers=headers,
                                        timeout=self.timeout_seconds)
            response.raise_for_status()
            try:
                return JevDecision.model_validate(response.json())
            except (ValueError, TypeError, ValidationError) as exc:
                raise JevInvalidResponse() from exc
        request = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(safe_payload, ensure_ascii=False)},
            ],
            "response_format": ({"type": "json_schema", "json_schema": {
                "name": "routing_decision", "strict": True,
                "schema": JevDecision.model_json_schema(),
            }}),
            "temperature": 0,
            "max_tokens": 96,
        }
        response = self.client.post(self.endpoint, json=request, headers=headers,
                                    timeout=self.timeout_seconds)
        response.raise_for_status()
        try:
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("content must be JSON text")
            return JevDecision.model_validate_json(content)
        except (ValueError, KeyError, IndexError, TypeError, ValidationError) as exc:
            raise JevInvalidResponse() from exc

