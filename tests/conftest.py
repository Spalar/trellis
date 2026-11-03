"""Shared test setup.

Every test gets a private TRELLIS_DATA_DIR unless it sets its own, so no test
writes project registrations, sync status or usage stats into the real
~/.trellis (integration tests used to leave entries there).
"""

import pytest


@pytest.fixture(autouse=True)
def _isolated_trellis_data_dir(monkeypatch, tmp_path_factory):
    monkeypatch.setenv("TRELLIS_DATA_DIR", str(tmp_path_factory.mktemp("trellis-data")))
