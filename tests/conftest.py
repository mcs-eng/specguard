"""Shared pytest fixtures for the SpecGuard gate suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures_pdf import write_spec_pdf


@pytest.fixture(scope="session")
def spec_pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Path to the fictional four-page specification PDF."""
    return write_spec_pdf(tmp_path_factory.mktemp("fixtures"))
