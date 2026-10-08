"""Shared fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):  # noqa: ANN001
    """Load custom_components/ in every test."""
    return


@pytest.fixture(autouse=True)
def allow_local_tcp(socket_enabled):  # noqa: ANN001
    """The simulated panel is a real TCP server on 127.0.0.1."""
    return


@pytest.fixture(autouse=True)
def fast_timing(monkeypatch):  # noqa: ANN001
    """Short timeouts for the client inside Home Assistant."""
    from custom_components.telenot import const

    from .test_client import FAST

    monkeypatch.setattr(const, "CLIENT_TIMING", FAST)
    monkeypatch.setattr(const, "CONNECT_WAIT", 3.0)
    monkeypatch.setattr(const, "TALK_WAIT", 3.0)
