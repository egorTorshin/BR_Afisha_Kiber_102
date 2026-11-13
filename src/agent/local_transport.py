"""llama-server Chat Completions transports; models only propose data and tool calls."""

import json

import httpx

from src.agent.heavy_llm import HeavyFinal, HeavyStep, ModelFunctionCall, _SYSTEM
from src.agent.light_llm import LightModelOutput, _PROMPT


def _post(base_url: str, body: dict, timeout: float, client: httpx.Client | None) -> dict:
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("LOCAL_LLM_BASE_URL must be an HTTP URL")
    sender = client or httpx.Client()
    try:
        response = sender.post(base_url.rstrip("/") + "/chat/completions", json=body, timeout=timeout)
        response.raise_for_status()
        return response.json()
    finally:
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
        return LightModelOutput.model_validate_json(content), *_usage(response)


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
            raise ValueError("missing Heavy JSON")
        return HeavyStep(final=HeavyFinal.model_validate_json(content),
                         input_tokens=tokens[0], output_tokens=tokens[1])

