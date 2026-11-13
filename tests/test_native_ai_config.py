import pytest

from src.agent.decision_router import DecisionRouter
from src.config import Settings


def test_native_ai_uses_configured_direct_endpoints():
    settings = Settings(ai_mode="native-ai", jev_enabled=True,
                        jev_base_url="http://127.0.0.1:8081/v1/",
                        jev_model="qwen2.5-1.5b-router",
                        local_llm_base_url="http://127.0.0.1:8082/v1")
    router = DecisionRouter(settings)
    assert router.jev.endpoint == "http://127.0.0.1:8081/v1/chat/completions"
    assert router.jev.llama_schema is True


@pytest.mark.parametrize("mode", ["no-ai", "local-ai", "native-ai"])
def test_all_modes_remain_available(mode):
    kwargs = {"jev_base_url": "http://127.0.0.1:8081/v1",
              "local_llm_base_url": "http://127.0.0.1:8082/v1"} if mode == "native-ai" else {}
    assert Settings(ai_mode=mode, **kwargs).ai_mode == mode


@pytest.mark.parametrize("field", ["jev_base_url", "local_llm_base_url"])
def test_native_ai_requires_both_http_endpoints(field):
    kwargs = {"jev_base_url": "http://127.0.0.1:8081/v1",
              "local_llm_base_url": "http://127.0.0.1:8082/v1"}
    kwargs[field] = ""
    with pytest.raises(ValueError, match=field.upper()):
        Settings(ai_mode="native-ai", **kwargs)


def test_model_prices_must_be_an_object():
    with pytest.raises(ValueError, match="JSON object"):
        Settings(model_prices=[])

