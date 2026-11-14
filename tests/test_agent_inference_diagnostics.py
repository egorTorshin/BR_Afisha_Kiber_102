import json

import pytest

from src.agent.diagnostics import InferenceRecorder, activate, deactivate
from src.agent.local_transport import LocalLightTransport


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, content):
        self.content = content

    def post(self, *args, **kwargs):
        return FakeResponse({"choices": [{"message": {"content": self.content},
                                          "finish_reason": "stop"}],
                             "usage": {"prompt_tokens": 17, "completion_tokens": 9}})


def test_local_inference_trace_records_safe_schema_and_usage_metadata():
    recorder = InferenceRecorder("req-1", "session-1", 3)
    token = activate(recorder)
    try:
        transport = LocalLightTransport("http://localhost:8082/v1", client=FakeClient(
            json.dumps({"intent": "clarify", "constraint_patch": {},
                        "tool_proposal": None, "clarification": "Уточните город"}, ensure_ascii=False)))
        transport.parse(model="qwen2.5-3b-local", payload={"message": "Тест"}, timeout=1)
    finally:
        deactivate(token)

    row = recorder.rows[0]
    assert row["request_id"] == "req-1"
    assert row["session_id"] == "session-1"
    assert row["turn_ref"] == 3
    assert row["route"] == "LIGHT"
    assert row["model_alias"] == "qwen2.5-3b-local"
    assert row["prompt_tokens"] == 17 and row["completion_tokens"] == 9
    assert row["finish_reason"] == "stop"
    assert row["http_status"] == 200
    assert row["json_parse"] == "success"
    assert row["schema_validation"] == "success"
    assert "message" not in row and "completion" not in row


def test_local_inference_trace_marks_schema_failure_without_raw_output():
    recorder = InferenceRecorder("req-2", "session-2", 1)
    token = activate(recorder)
    try:
        transport = LocalLightTransport("http://localhost:8082/v1", client=FakeClient('{"intent":"bogus"}'))
        with pytest.raises(Exception):
            transport.parse(model="qwen2.5-3b-local", payload={"message": "synthetic"}, timeout=1)
    finally:
        deactivate(token)

    row = recorder.rows[0]
    assert row["json_parse"] == "success"
    assert row["schema_validation"] == "failure"
    assert row["error_category"] == "schema_validation"
    assert row["validation_path"].startswith("intent")
    assert "bogus" not in str(row)
