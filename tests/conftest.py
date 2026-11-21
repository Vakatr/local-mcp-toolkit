"""Shared pytest configuration."""

from __future__ import annotations

import pytest

_MANAGED_ENV = (
    "MCP_FS_ROOT",
    "MCP_FS_READ_ROOT",
    "MCP_RUNNER_ROOT",
    "MCP_RUNNER_MAX_TIMEOUT",
    "DELEGATE_BASE_URL",
    "DELEGATE_API_KEY",
    "DELEGATE_DEFAULT_MODEL",
    "DELEGATE_TIMEOUT",
    "DELEGATE_MAX_PROMPT",
    "DELEGATE_MAX_RESPONSE",
)


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's real MCP configuration out of the test suite."""
    for name in _MANAGED_ENV:
        monkeypatch.delenv(name, raising=False)
