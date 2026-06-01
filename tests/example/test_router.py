import fastapi.testclient

import app.entrypoints.fastapi
from app.common import http_client, mongo

client = fastapi.testclient.TestClient(app.entrypoints.fastapi.app)


def test_root_success():
    response = client.get("/example/test")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_db_query_success(mocker):
    mock_db = mocker.AsyncMock()
    mock_db.example.insert_one.return_value = None
    mock_db.example.find_one.return_value = {"foo": "bar", "id": 123}

    app.entrypoints.fastapi.app.dependency_overrides[mongo.get_db] = lambda: mock_db

    try:
        response = client.get("/example/db")

        assert response.status_code == 200
        assert response.json() == {"ok": {"foo": "bar", "id": 123}}

        mock_db.example.insert_one.assert_called_once()
    finally:
        app.entrypoints.fastapi.app.dependency_overrides = {}


def test_http_query_success(mocker):
    mock_client = mocker.AsyncMock()
    mock_client.get.return_value.status_code = 200

    app.entrypoints.fastapi.app.dependency_overrides[
        http_client.create_async_client
    ] = lambda: mock_client

    try:
        response = client.get("/example/http")

        assert response.status_code == 200
        assert response.json() == {"ok": 200}
    finally:
        app.entrypoints.fastapi.app.dependency_overrides = {}
