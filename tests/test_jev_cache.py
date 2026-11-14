"""Cache must preserve Jev routing and never cross dialogue boundaries."""

from src.agent.decision_router import DecisionRouter
from src.agent.jev import JevDecision
from src.agent.jev_cache import eligible, key_for
from src.agent.routing import RoutingFeatureExtractor
from src.agent.schemas import ModelRoute
from src.agent.security import SecurityFlags
from src.agent.state import DialogueState, DialogueTurn
from src.config import Settings


class FakeJev:
    def __init__(self):
        self.calls = 0

    def decide(self, _payload):
        self.calls += 1
        return JevDecision(route=ModelRoute.LIGHT, confidence=0.95, reason_code="simple_search")


class FakeCache:
    def __init__(self, broken=False):
        self.items = {}
        self.broken = broken

    def get(self, key):
        if self.broken:
            raise ConnectionError("Redis unavailable")
        return self.items.get(key)

    def set(self, key, decision):
        if self.broken:
            raise ConnectionError("Redis unavailable")
        self.items[key] = decision


def features(message="Какие концерты в Уфе?", state=None):
    return RoutingFeatureExtractor().extract(message, state or DialogueState(conversation_id="c"), SecurityFlags())


def settings():
    return Settings(ai_mode="local-ai", jev_enabled=True, jev_endpoint="http://router/decide")


def test_first_turn_jev_decision_cache_hit_and_catalog_invalidation():
    query = features()
    assert eligible(query)
    jev, cache = FakeJev(), FakeCache()
    router = DecisionRouter(settings(), jev=jev, cache=cache)
    first, second = router.decide(query), router.decide(query)
    assert (first.cache_status, second.cache_status, jev.calls) == ("miss", "hit", 1)
    assert first.route == second.route == ModelRoute.LIGHT
    assert all("event_id" not in value.model_dump_json() for value in cache.items.values())
    assert key_for(query, model="m", prompt_version="v1", catalog_version="v1") != key_for(
        query, model="m", prompt_version="v1", catalog_version="v2")


def test_dialogue_and_redis_failure_keep_live_jev():
    state = DialogueState(conversation_id="c", turns=[DialogueTurn(role="user", content="концерты")])
    assert not eligible(features(state=state))
    jev = FakeJev()
    router = DecisionRouter(settings(), jev=jev, cache=FakeCache(broken=True))
    assert router.decide(features()).cache_status == "error"
    assert router.decide(features(state=state)).cache_status == "ineligible"
    assert jev.calls == 2
