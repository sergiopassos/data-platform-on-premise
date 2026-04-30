"""Tests for the session provider accessors."""
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


class _FakeSessionStore(dict):
    def get(self, key, default=None):
        return super().get(key, default)

    def set(self, key, value):
        self[key] = value


@pytest.fixture(autouse=True)
def mock_chainlit():
    """Stub chainlit into sys.modules so session.py can be imported without the server."""
    fake_cl = types.ModuleType("chainlit")
    fake_cl.user_session = _FakeSessionStore()
    sys.modules.setdefault("chainlit", fake_cl)
    # Also stub chainlit sub-modules that may be imported
    for sub in ("chainlit.input_widget",):
        sys.modules.setdefault(sub, types.ModuleType(sub))
    yield fake_cl
    # Do not remove — other tests in the same process may have imported it too


@pytest.fixture
def session_store(mock_chainlit):
    store = _FakeSessionStore()
    mock_chainlit.user_session = store
    # Reload session module to pick up the new store reference
    import importlib

    import portal.agent.session as session_mod
    importlib.reload(session_mod)
    return store, session_mod


class TestSessionAccessors:
    def test_get_provider_returns_none_when_unset(self, session_store):
        _, session_mod = session_store
        assert session_mod.get_provider() is None

    def test_set_and_get_provider_roundtrip(self, session_store):
        _, session_mod = session_store

        with patch.object(session_mod, "build_from_name") as mock_build:
            fake_provider = MagicMock()
            fake_provider.name = "fallback"
            mock_build.return_value = fake_provider

            result = session_mod.set_provider_by_name("fallback")

        assert result is fake_provider
        assert session_mod.get_provider() is fake_provider

    def test_clear_provider_sets_none(self, session_store):
        store, session_mod = session_store
        store["llm_provider"] = MagicMock()
        session_mod.clear_provider()
        assert session_mod.get_provider() is None

    def test_set_unknown_provider_raises_value_error(self, session_store):
        _, session_mod = session_store

        with patch.object(session_mod, "build_from_name") as mock_build:
            mock_build.side_effect = ValueError("Unknown provider 'bad'")
            with pytest.raises(ValueError, match="Unknown provider"):
                session_mod.set_provider_by_name("bad")
