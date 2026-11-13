"""Fail-safe compute router. It neither selects tools nor handles user content."""

import math
import time
from typing import Literal, Protocol

import httpx
from pydantic import Field, ValidationError, model_validator

from src.agent.jev import JevAdapter, JevDecision, JevInvalidResponse, ReasonCode
from src.agent.schemas import ContractModel, ModelRoute, RoutingFeatures
from src.config import Settings


class JevClient(Protocol):
    def decide(self, payload: dict[str, str | int | bool]) -> JevDecision: ...


LiteralRouterError = Literal["configuration_error", "timeout", "transport_error", "invalid_response"]


class RouterDecision(ContractModel):
    router_payload: dict[str, str | int | bool]
    route: ModelRoute
    jev_route: ModelRoute | None = None
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    reason_code: ReasonCode
    latency_ms: int = Field(ge=0)
    router_error: LiteralRouterError | None = None
    fallback: bool = False

    @model_validator(mode="after")
    def validate_safe_payload(self) -> "RouterDecision":
        if set(self.router_payload) != set(RoutingFeatures.model_fields):
            raise ValueError("router trace payload must contain only sanitized features")
        if RoutingFeatures.model_validate(self.router_payload).to_router_payload() != self.router_payload:
            raise ValueError("router trace payload has invalid values")
        return self


class FallbackRouter:
    """Deterministic complexity policy for local and test mode."""

    def decide(self, features: RoutingFeatures) -> tuple[ModelRoute, ReasonCode]:
        if any((features.possible_prompt_injection, features.possible_jailbreak,
                features.possible_prompt_leak_attempt, features.possible_sql_injection)):
            return ModelRoute.HEAVY, "security_risk"
        if features.intent == "planner" or features.requires_multi_step:
            return ModelRoute.HEAVY, "multi_step_planning"
        if features.requires_comparison and not (features.requires_dialogue_context and
                                                 features.has_previous_results):
            return ModelRoute.HEAVY, "comparison_reasoning"
        if features.requires_clarification or features.dialogue_depth > 5:
            return ModelRoute.HEAVY, "complex_dialogue"
        if features.constraint_count >= 4:
            return ModelRoute.HEAVY, "constraint_conflict"
        if features.requires_temporal_reasoning and features.requires_geo_reasoning:
            return ModelRoute.HEAVY, "temporal_reasoning"
        if features.requires_dialogue_context:
            return ModelRoute.LIGHT, "simple_followup"
        if features.intent in {"get_event", "get_venue_schedule"}:
            return ModelRoute.LIGHT, "structured_extraction"
        return ModelRoute.LIGHT, "simple_search"


class DecisionRouter:
    def __init__(self, settings: Settings, *, jev: JevClient | None = None,
                 fallback: FallbackRouter | None = None) -> None:
        self.settings = settings
        endpoint = (settings.jev_base_url.rstrip("/") + "/chat/completions"
                    if settings.ai_mode == "native-ai" else settings.jev_endpoint)
        self.jev = jev or (JevAdapter(endpoint, settings.jev_model,
                                     settings.jev_api_key,
                                     timeout_seconds=settings.jev_timeout_seconds,
                                     llama_schema=settings.ai_mode == "native-ai")
                           if settings.jev_enabled else None)
        self.fallback = fallback or FallbackRouter()

    def route(self, features: RoutingFeatures) -> ModelRoute:
        return self.decide(features).route

    def decide(self, features: RoutingFeatures) -> RouterDecision:
        start = time.perf_counter()
        payload = features.to_router_payload()

        def result(route: ModelRoute, reason: ReasonCode, *, jev_route: ModelRoute | None = None,
                   confidence: float | None = None, error: LiteralRouterError | None = None,
                   fallback: bool = False) -> RouterDecision:
            return RouterDecision(router_payload=payload, route=route, jev_route=jev_route,
                                  confidence=confidence, reason_code=reason,
                                  latency_ms=max(0, round((time.perf_counter() - start) * 1000)),
                                  router_error=error, fallback=fallback)

        if any((features.possible_prompt_injection, features.possible_jailbreak,
                features.possible_prompt_leak_attempt, features.possible_sql_injection)):
            return result(ModelRoute.HEAVY, "security_risk", fallback=True)

        if self.settings.ai_mode == "no-ai" or not self.settings.jev_enabled:
            route, reason = self.fallback.decide(features)
            return result(route, reason, fallback=True)

        if (not (self.settings.jev_base_url if self.settings.ai_mode == "native-ai"
                 else self.settings.jev_endpoint) or not self.settings.jev_model or
                not math.isfinite(self.settings.jev_confidence_threshold) or
                not 0 <= self.settings.jev_confidence_threshold <= 1 or
                not math.isfinite(self.settings.jev_timeout_seconds) or
                self.settings.jev_timeout_seconds <= 0):
            return result(ModelRoute.HEAVY, "router_failure",
                          error="configuration_error", fallback=True)

        try:
            assert self.jev is not None
            # Validate even injected adapter output; no free-form text controls routing.
            decision = JevDecision.model_validate(self.jev.decide(payload))
        except httpx.TimeoutException:
            return result(ModelRoute.HEAVY, "router_failure", error="timeout", fallback=True)
        except (JevInvalidResponse, ValidationError):
            return result(ModelRoute.HEAVY, "router_failure", error="invalid_response", fallback=True)
        except Exception:
            return result(ModelRoute.HEAVY, "router_failure", error="transport_error", fallback=True)

        if decision.confidence < self.settings.jev_confidence_threshold:
            return result(ModelRoute.HEAVY, "low_confidence", jev_route=decision.route,
                          confidence=decision.confidence, fallback=True)
        # Keep the same deterministic safety guard for native direct Jev as for the
        # Docker gateway. A tiny router can emit a valid but semantically wrong route.
        rule_route, rule_reason = self.fallback.decide(features)
        obvious_simple = (features.intent == "search_events" and
                          features.constraint_count <= 2 and features.dialogue_depth == 0 and
                          not any((features.requires_multi_step, features.requires_comparison,
                                  features.requires_clarification,
                                  features.requires_dialogue_context,
                                  features.possible_prompt_injection, features.possible_jailbreak,
                                  features.possible_prompt_leak_attempt,
                                  features.possible_sql_injection)))
        obvious_light_followup = (
            features.intent == "search_events" and features.has_previous_results and
            features.requires_dialogue_context and not features.requires_multi_step and
            not features.requires_clarification and features.constraint_count <= 3 and
            features.dialogue_depth <= 5
        )
        obvious_heavy = (features.intent == "planner" or features.requires_multi_step or
                         (features.requires_comparison and not (
                             features.requires_dialogue_context and features.has_previous_results)) or
                         features.requires_clarification or
                         features.constraint_count >= 4)
        if ((obvious_simple or obvious_light_followup) and decision.route != ModelRoute.LIGHT) or \
                (obvious_heavy and decision.route != ModelRoute.HEAVY):
            return result(rule_route, rule_reason, jev_route=decision.route,
                          confidence=decision.confidence)
        return result(decision.route, decision.reason_code, jev_route=decision.route,
                      confidence=decision.confidence)

