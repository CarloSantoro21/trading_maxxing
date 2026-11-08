from fastapi.testclient import TestClient
from tm_forecast.main import app


def test_health():
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "forecast"
