from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint_returns_payload():
    client = TestClient(app)
    response = client.get('/health')
    assert response.status_code == 200
    payload = response.json()
    assert 'status' in payload
    assert 'db' in payload
