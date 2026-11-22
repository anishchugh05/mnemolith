"""Tests marked `engine` start real engines and take several seconds, so `python -m pytest tests` skips them.
Run them with `python -m pytest tests -m engine` (the release checklist does)."""
import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "engine: starts real engines (slow); run with -m engine")


def pytest_collection_modifyitems(config, items):
    if "engine" in (config.option.markexpr or ""):  # the person chose whether to run them (-m engine / "not engine")
        return
    skip = pytest.mark.skip(reason="starts real engines; run with -m engine")
    for item in items:
        if "engine" in item.keywords:
            item.add_marker(skip)
