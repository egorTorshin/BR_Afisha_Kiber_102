"""llama-server Chat Completions transports; models only propose data and tool calls."""

import json
import time

import httpx

from src.agent.diagnostics import current as current_recorder, elapsed_ms, latest_index, update_current

from src.agent.heavy_llm import (HeavyFinal, HeavyStep, ModelFunctionCall, _SYSTEM,
                                 normalize_heavy_final_payload)
from src.agent.light_llm import LightModelOutput, _PROMPT


def _post(base_url: str, body: dict, timeout: float, client: httpx.Client | None) -> dict:
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("LOCAL_LLM_BASE_URL must be an HTTP URL")
    sender = client or httpx.Client()
    started = time.perf_counter()
    recorder = current_recorder()
    row = {"route": "LIGHT" if (body.get("response_format", {}).get("json_schema", {}).get("name") == "light_output") else "HEAVY",
           "model_alias": body.get("model"), "prompt_tokens": 0, "completion_tokens": 0,
           "completion_length": 0, "finish_reason": None, "http_status": None,
           "json_parse": "not_checked", "schema_validation": "not_checked",
           "tool_name_validation": "not_applicable", "tool_argument_validation": "not_applicable",
           "error_category": None}
    index = recorder.record(row) if recorder else None
    try:
        response = sender.post(base_url.rstrip("/") + "/chat/completions", json=body, timeout=timeout)
        if recorder:
            recorder.update(index, http_status=response.status_code,
                            inference_latency_ms=elapsed_ms(started))
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices") or []
        choice = choices[0] if choices else {}
        message = choice.get("message") or {}
        content = message.get("content")
        pieces = [content] if isinstance(content, str) else []
        calls = message.get("tool_calls") or []
        pieces.extend(call.get("function", {}).get("arguments", "") for call in calls)
        validation_started = time.perf_counter()
        parsed = True
        for piece in pieces:
            if piece:
                try:
                    json.loads(piece)
                except (ValueError, TypeError):
                    parsed = False
        usage = data.get("usage") or {}
        if recorder:
            finish_reason = choice.get("finish_reason")
            if calls:
                recorder.update(index, schema_validation="not_applicable")
            recorder.update(index,
                prompt_tokens=max(0, int(usage.get("prompt_tokens") or 0)),
                completion_tokens=max(0, int(usage.get("completion_tokens") or 0)),
                completion_length=sum(len(piece) for piece in pieces if isinstance(piece, str)),
                finish_reason=finish_reason,
                json_parse="success" if parsed else "failure",
                error_category=None if parsed else (
                    "max_tokens_truncation" if finish_reason == "length" else "json_syntax"))
            recorder.update(index, json_validation_latency_ms=elapsed_ms(validation_started))
        return data
    except httpx.HTTPStatusError as exc:
        if recorder:
            recorder.update(index, http_status=exc.response.status_code,
                            latency_ms=elapsed_ms(started), error_category="http_status")
        raise
    except (ValueError, TypeError) as exc:
        if recorder:
            recorder.update(index, latency_ms=elapsed_ms(started), json_parse="failure",
                            error_category="invalid_response_json")
        raise
    except Exception:
        if recorder:
            recorder.update(index, latency_ms=elapsed_ms(started), error_category="transport_error")
        raise
    finally:
        if recorder:
            recorder.update(index, latency_ms=elapsed_ms(started))
        if client is None:
            sender.close()


def _usage(response: dict) -> tuple[int, int]:
    usage = response.get("usage") or {}
    return max(0, int(usage.get("prompt_tokens") or 0)), max(0, int(usage.get("completion_tokens") or 0))


class LocalLightTransport:
    def __init__(self, base_url: str, max_tokens: int = 384, client: httpx.Client | None = None):
        self.base_url, self.max_tokens, self.client = base_url, max_tokens, client

    def parse(self, *, model: str, payload: dict, timeout: float) -> tuple[object, int, int]:
        response = _post(self.base_url, {
            "model": model,
            "messages": [{"role": "system", "content": _PROMPT},
                         {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "light_output", "strict": True,
                "schema": LightModelOutput.model_json_schema(),
            }},
            "temperature": 0, "max_tokens": self.max_tokens,
        }, timeout, self.client)
        content = response["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("missing Light JSON")
        validation_started = time.perf_counter()
        try:
            output = LightModelOutput.model_validate_json(content)
        except Exception as exc:
            update_current(latest_index(), structured_validation_latency_ms=elapsed_ms(validation_started))
            path = ".".join(str(part) for part in exc.errors()[0].get("loc", ())) if hasattr(exc, "errors") and exc.errors() else None
            issue = exc.errors(include_input=False)[0] if hasattr(exc, "errors") and exc.errors() else {}
            reason_code = (issue.get("ctx", {}).get("error").args[0]
                           if issue.get("type") == "value_error" and issue.get("ctx", {}).get("error")
                           else None)
            update_current(latest_index(), schema_validation="failure",
                           error_category="schema_validation", validation_error=type(exc).__name__,
                           validation_path=path, reason_code=reason_code,
                           validation_layer="pydantic_business_invariant" if reason_code else "pydantic_schema")
            raise
        update_current(latest_index(), schema_validation="success",
                       structured_validation_latency_ms=elapsed_ms(validation_started))
        return output, *_usage(response)


def _chat_history(messages: list[dict]) -> list[dict]:
    """Convert the compact current-step context into Chat Completions tool turns."""
    result = [{"role": "system", "content": _SYSTEM + "\nOutput terminal JSON with exactly: status, "
               "basis_event_ids, short_explanation, and optional constraint_relaxation."}]
    for item in messages:
        if item.get("type") == "local_tool_calls":
            result.append({"role": "assistant", "content": None, "tool_calls": item["tool_calls"]})
        elif item.get("type") == "function_call_output":
            result.append({"role": "tool", "tool_call_id": item["call_id"],
                           "content": item["output"]})
        elif item.get("role") in {"user", "assistant"}:
            result.append({"role": item["role"], "content": item["content"]})
    return result


class LocalHeavyTransport:
    def __init__(self, base_url: str, max_tokens: int = 512, client: httpx.Client | None = None):
        self.base_url, self.max_tokens, self.client = base_url, max_tokens, client

    def step(self, *, model: str, messages: list[dict], schemas: dict,
             timeout: float) -> HeavyStep:
        tools = [{"type": "function", "function": {
            "name": name, "description": f"Registered {name} application tool",
            "parameters": schema.model_json_schema()}}
            for name, schema in schemas.items()]
        require_tool = any(
            item.get("role") == "user" and '"must_call_tool":true' in str(item.get("content"))
            for item in messages
        )
        body = {
            "model": model, "messages": _chat_history(messages), "tools": tools,
            "tool_choice": "required" if require_tool else "auto", "parallel_tool_calls": False,
            "temperature": 0, "max_tokens": self.max_tokens,
        }
        if not require_tool:
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "heavy_final", "strict": True,
                "schema": HeavyFinal.model_json_schema(),
            }}
        response = _post(self.base_url, body, timeout, self.client)
        message = response["choices"][0]["message"]
        raw_calls = message.get("tool_calls") or []
        tokens = _usage(response)
        if raw_calls:
            calls = []
            for call in raw_calls:
                function = call["function"]
                try:
                    arguments = json.loads(function["arguments"])
                except (TypeError, ValueError):
                    arguments = None
                calls.append(ModelFunctionCall(call_id=call["id"], name=function["name"],
                                               arguments=arguments))
            return HeavyStep(calls=calls,
                             output_items=[{"type": "local_tool_calls", "tool_calls": raw_calls}],
                             input_tokens=tokens[0], output_tokens=tokens[1])
        content = message.get("content")
        if not isinstance(content, str):
            update_current(latest_index(), json_parse="failure", schema_validation="failure",
                           error_category="missing_content")
            raise ValueError("missing Heavy JSON")
        validation_started = time.perf_counter()
        try:
            raw_payload = json.loads(content)
            normalized_payload, normalized = normalize_heavy_final_payload(raw_payload)
            final = HeavyFinal.model_validate(normalized_payload)
        except Exception as exc:
            update_current(latest_index(), structured_validation_latency_ms=elapsed_ms(validation_started))
            path = ".".join(str(part) for part in exc.errors()[0].get("loc", ())) if hasattr(exc, "errors") and exc.errors() else None
            issue = exc.errors(include_input=False)[0] if hasattr(exc, "errors") and exc.errors() else {}
            reason_code = (issue.get("ctx", {}).get("error").args[0]
                           if issue.get("type") == "value_error" and issue.get("ctx", {}).get("error")
                           else None)
            update_current(latest_index(), schema_validation="failure",
                           error_category="schema_validation", validation_error=type(exc).__name__,
                           validation_path=path, reason_code=reason_code,
                           validation_layer="pydantic_business_invariant" if reason_code else "pydantic_schema")
            raise
        update_current(latest_index(), schema_validation="success",
                       structured_validation_latency_ms=elapsed_ms(validation_started),
                       constraint_relaxation_normalized=normalized,
                       normalization_reason=("non_conflict_relaxation_removed" if normalized else None))
        return HeavyStep(final=final,
                         input_tokens=tokens[0], output_tokens=tokens[1])

