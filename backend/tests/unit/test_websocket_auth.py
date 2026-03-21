import asyncio

from app.core.security import create_access_token
from app.db.models.user import User
from app.main import _authorize_websocket, _resolve_websocket_token, settings


class _FakeWebSocket:
    def __init__(self, *, headers: dict[str, str] | None = None, query_params: dict[str, str] | None = None) -> None:
        self.headers = headers or {}
        self.query_params = query_params or {}
        self.closed_code: int | None = None

    async def close(self, code: int) -> None:
        self.closed_code = code


def test_resolve_websocket_token_prefers_authorization_header() -> None:
    ws = _FakeWebSocket(
        headers={'authorization': 'Bearer header-token'},
        query_params={'token': 'query-token'},
    )
    assert _resolve_websocket_token(ws) == 'header-token'


def test_authorize_websocket_rejects_missing_token(monkeypatch) -> None:
    ws = _FakeWebSocket()
    monkeypatch.setattr(settings, 'ws_require_auth', True)
    assert asyncio.run(_authorize_websocket(ws)) is False
    assert ws.closed_code == 1008


def test_authorize_websocket_accepts_valid_token_with_active_user(monkeypatch) -> None:
    ws = _FakeWebSocket(headers={'authorization': f'Bearer {create_access_token("42", "admin")}'})
    monkeypatch.setattr(settings, 'ws_require_auth', True)

    class _FakeSession:
        def get(self, _model, user_id: int):
            if user_id != 42:
                return None
            return User(id=42, email='ws@local.dev', hashed_password='x', role='admin', is_active=True)

        def close(self) -> None:
            return

    monkeypatch.setattr('app.main.SessionLocal', lambda: _FakeSession())

    assert asyncio.run(_authorize_websocket(ws)) is True
    assert ws.closed_code is None
