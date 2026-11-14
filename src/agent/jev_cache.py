"""Optional, fail-open Redis cache for context-free Jev routing decisions."""

import hashlib
import json
import socket
from urllib.parse import urlsplit

from src.agent.jev import JevDecision, _SYSTEM_PROMPT
from src.agent.schemas import RoutingFeatures


CACHE_SCHEMA = "jev-decision-v1"


def eligible(features: RoutingFeatures) -> bool:
    return (
        features.intent == "search_events"
        and features.dialogue_depth == 0
        and features.dialogue_turns == 0
        and not features.has_previous_results
        and not features.requires_dialogue_context
        and not features.requires_clarification
        and not features.requires_multi_step
        and not features.requires_comparison
        and not any((features.possible_prompt_injection, features.possible_jailbreak,
                     features.possible_prompt_leak_attempt, features.possible_sql_injection))
    )


def key_for(features: RoutingFeatures, *, model: str, prompt_version: str,
            catalog_version: str) -> str:
    # A digest conceals even scalar features such as the message length.
    payload = {
        "schema": CACHE_SCHEMA,
        "model": model,
        "prompt": hashlib.sha256((_SYSTEM_PROMPT + prompt_version).encode()).hexdigest(),
        "catalog": catalog_version,
        "features": features.to_router_payload(),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=False).encode()).hexdigest()
    return f"afisha:{CACHE_SCHEMA}:{digest}"


class RedisJevCache:
    """Tiny RESP client so the optional cache adds no production dependency."""

    def __init__(self, url: str, ttl_seconds: int, timeout_seconds: float = 0.05):
        parsed = urlsplit(url)
        if parsed.scheme != "redis" or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {"", "/0"}:
            raise ValueError("REDIS_URL must be redis://host:port/0 without credentials")
        self.host = parsed.hostname
        self.port = parsed.port or 6379
        self.ttl_seconds = ttl_seconds
        self.timeout_seconds = timeout_seconds

    def _command(self, *parts: str) -> bytes | None:
        encoded = [part.encode("utf-8") for part in parts]
        command = f"*{len(parts)}\r\n".encode() + b"".join(
            f"${len(part)}\r\n".encode() + part + b"\r\n" for part in encoded)
        with socket.create_connection((self.host, self.port), self.timeout_seconds) as sock:
            sock.settimeout(self.timeout_seconds)
            sock.sendall(command)
            reader = sock.makefile("rb")
            header = reader.readline()
            if not header:
                raise ConnectionError("Redis closed connection")
            if header[:1] == b"-":
                raise ConnectionError("Redis command failed")
            if header[:1] == b"+":
                return header[1:-2]
            if header[:1] != b"$":
                raise ValueError("unexpected Redis response")
            length = int(header[1:-2])
            if length == -1:
                return None
            if length < 0 or length > 1024:
                raise ValueError("Redis value too large")
            data = reader.read(length + 2)
            if len(data) != length + 2 or not data.endswith(b"\r\n"):
                raise ValueError("truncated Redis response")
            return data[:-2]

    def get(self, key: str) -> JevDecision | None:
        raw = self._command("GET", key)
        return JevDecision.model_validate_json(raw) if raw is not None else None

    def set(self, key: str, decision: JevDecision) -> None:
        self._command("SET", key, decision.model_dump_json(), "EX", str(self.ttl_seconds))
