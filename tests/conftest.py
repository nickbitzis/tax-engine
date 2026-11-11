from __future__ import annotations

import pytest

from taxengine import sim as simmod


@pytest.fixture(scope="session")
def sim():
    """A 10% scale run of the year (about 20,000 orders), shared by the slower tests."""
    return simmod.run(scale=0.1)
