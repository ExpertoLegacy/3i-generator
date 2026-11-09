"""Most tests exercise one part directly, so the approval locks are off unless a test turns them on."""
import pytest


@pytest.fixture(autouse=True)
def _locks_off(monkeypatch, request):
    from tests import test_ai  # noqa: F401 - puts the app in test mode before it is imported
    from app import web

    monkeypatch.setattr(web, "GATING", request.node.get_closest_marker("gated") is not None)


def pytest_configure(config):
    config.addinivalue_line("markers", "gated: run with the part-by-part approval locks on")
