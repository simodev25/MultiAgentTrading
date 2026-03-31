import asyncio
from unittest.mock import MagicMock, patch

import pytest

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


@pytest.mark.asyncio
async def test_authorize_websocket_accepts_valid_token_with_active_user(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'secret_key', 'test-stable-ws-key-suite')
    monkeypatch.setattr(settings, 'ws_require_auth', True)

    token = create_access_token("42", "admin")
    ws = _FakeWebSocket(headers={'authorization': f'Bearer {token}'})

    fake_user = User(id=42, email='ws@local.dev', hashed_password='x', role='admin', is_active=True)
    mock_session = MagicMock()
    mock_session.get.return_value = fake_user

    monkeypatch.setattr('app.main._get_session', lambda: mock_session)

    # Also patch the settings used by security.py's create_access_token / jwt.decode
    from app.core.config import get_settings as _gs
    _gs_settings = _gs()
    monkeypatch.setattr(_gs_settings, 'secret_key', 'test-stable-ws-key-suite')

    # Re-create token with the patched key
    token = create_access_token("42", "admin")
    ws = _FakeWebSocket(headers={'authorization': f'Bearer {token}'})

    result = await _authorize_websocket(ws)

    assert result is True
    assert ws.closed_code is None
    mock_session.get.assert_called_once_with(User, 42)
    mock_session.close.assert_called_once()
