"""
tests/test_web_dashboard_auth.py — verifies the dashboard token gate.
"""
import os

import pytest


@pytest.fixture
def client_no_token(monkeypatch):
    monkeypatch.delenv("WEB_DASHBOARD_TOKEN", raising=False)
    from fastapi.testclient import TestClient
    from ui.web_dashboard import app
    return TestClient(app)


@pytest.fixture
def client_with_token(monkeypatch):
    monkeypatch.setenv("WEB_DASHBOARD_TOKEN", "s3cret")
    from fastapi.testclient import TestClient
    from ui.web_dashboard import app
    return TestClient(app)


def test_root_open_when_no_token_set(client_no_token):
    resp = client_no_token.get("/")
    assert resp.status_code == 200
    assert "<html" in resp.text.lower()


def test_root_rejects_when_token_required_and_missing(client_with_token):
    resp = client_with_token.get("/")
    assert resp.status_code == 401


def test_root_rejects_wrong_token(client_with_token):
    resp = client_with_token.get("/?token=wrong")
    assert resp.status_code == 401


def test_root_accepts_correct_token(client_with_token):
    resp = client_with_token.get("/?token=s3cret")
    assert resp.status_code == 200


def test_websocket_rejects_missing_token(client_with_token):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client_with_token.websocket_connect("/ws"):
            pass


def test_websocket_accepts_correct_token(client_with_token):
    with client_with_token.websocket_connect("/ws?token=s3cret") as ws:
        # Connection should succeed; we don't expect any immediate message
        # in a unit test (the push loop runs every 2s in production).
        pass
