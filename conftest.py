"""Fixtures comunes; la lógica vive en `rsp_testkit.py` (raíz del repo)."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from rsp_testkit import SECRET, FakeBus  # noqa: E402


@pytest.fixture
def fake_bus() -> FakeBus:
    return FakeBus()


@pytest.fixture
def auth_env(monkeypatch):
    """Autenticación activa con un secreto conocido."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", SECRET)
    return SECRET
