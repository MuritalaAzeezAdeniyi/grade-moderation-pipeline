from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_validation_errors_use_error_shape():
    # Exercise FastAPI's validation exception handler with a deliberately bad
    # integer on a temporary route.
    from app.main import app

    @app.get("/__test_validation__/{value}")
    async def test_validation(value: int):
        return {"value": value}

    response = client.get("/__test_validation__/not-an-int")

    assert response.status_code == 400
    assert response.json() == {"error": "Invalid input"}
