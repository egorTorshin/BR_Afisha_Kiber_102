from fastapi.testclient import TestClient

from src.api import create_app
from src.config import Settings
from src.db.connection import Database
from src.utils.metrics import REGISTRY


def test_http_metrics_are_scrapable_and_have_bounded_labels(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'metrics.db'}")
    try:
        with TestClient(create_app(db, Settings())) as client:
            response = client.get('/health')
            assert response.status_code == 200
            metrics = client.get('/metrics')
            assert metrics.status_code == 200
            assert 'afisha_http_requests_total{channel="web",method="GET",status="200"}' in metrics.text
            assert 'afisha_http_request_duration_seconds_bucket' in metrics.text
            assert 'request_id' not in metrics.text
            assert 'user_id' not in metrics.text
    finally:
        db.close()


def test_metrics_collector_exposes_only_declared_metric_families():
    names = {family.name for family in REGISTRY.collect()}
    assert {'http_requests', 'http_request_duration_seconds',
            'bot_updates', 'ai_calls'} <= names
