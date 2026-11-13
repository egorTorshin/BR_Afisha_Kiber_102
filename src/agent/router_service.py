"""Private Jev gateway: only RoutingFeatures enter, only JevDecision leaves."""

import os
import subprocess

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.agent.jev import JevAdapter, JevDecision
from src.agent.decision_router import FallbackRouter
from src.agent.schemas import ModelRoute
from src.agent.schemas import RoutingFeatures


MODEL = "qwen2.5-1.5b-router"
LLAMA_URL = "http://127.0.0.1:8080"


def create_app(client: httpx.Client | None = None) -> FastAPI:
    app = FastAPI(title="Private Jev router", docs_url=None, redoc_url=None,
                  openapi_url=None)
    @app.exception_handler(RequestValidationError)
    async def invalid_payload(_request, _error):
        return JSONResponse(status_code=422, content={"error": "invalid_routing_features"})
    http = client or httpx.Client()
    adapter = JevAdapter(LLAMA_URL + "/v1/chat/completions", MODEL,
                         timeout_seconds=15, client=http, llama_schema=True)
    guard = FallbackRouter()

    @app.get("/health")
    def health():
        try:
            response = http.get(LLAMA_URL + "/health", timeout=2)
            response.raise_for_status()
            if response.json().get("status") != "ok":
                raise ValueError("model is not ready")
        except Exception as exc:
            raise HTTPException(503, "router model is not ready") from exc
        return {"status": "ok"}

    @app.post("/decide", response_model=JevDecision)
    def decide(features: RoutingFeatures):
        try:
            decision = adapter.decide(features.to_router_payload())
            rule_route, rule_reason = guard.decide(features)
            obvious_simple = (features.intent == "search_events" and
                              features.constraint_count <= 2 and features.dialogue_depth == 0 and
                              not (features.requires_temporal_reasoning and
                                   features.requires_geo_reasoning) and
                              not any((features.requires_multi_step, features.requires_comparison,
                                       features.requires_clarification, features.requires_dialogue_context,
                                       features.possible_prompt_injection, features.possible_jailbreak,
                                       features.possible_prompt_leak_attempt,
                                       features.possible_sql_injection)))
            obvious_heavy = (features.intent == "planner" or features.requires_multi_step or
                             features.requires_comparison or features.requires_clarification or
                             features.constraint_count >= 4)
            if (obvious_simple and decision.route != ModelRoute.LIGHT or
                    obvious_heavy and decision.route != ModelRoute.HEAVY):
                return JevDecision(route=rule_route, confidence=1.0, reason_code=rule_reason)
            return decision
        except Exception as exc:
            raise HTTPException(503, "router inference unavailable") from exc

    return app


def main() -> None:
    import uvicorn
    process = subprocess.Popen([
        os.getenv("LLAMA_SERVER_BIN", "llama-server"), "--model",
        os.getenv("ROUTER_MODEL_PATH", "models/qwen2.5-1.5b-instruct-q4_k_m.gguf"),
        "--alias", MODEL, "--host", "127.0.0.1", "--port", "8080",
        "--ctx-size", "1024", "--batch-size", "128", "--ubatch-size", "64",
        "--parallel", "1", "--n-gpu-layers", os.getenv("ROUTER_GPU_LAYERS", "0"),
        "--n-predict", "96",
    ])
    try:
        uvicorn.run(create_app(), host="0.0.0.0", port=8001)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    main()

