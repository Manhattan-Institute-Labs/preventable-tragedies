import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))


@pytest.fixture(autouse=True)
def _no_smi_data(monkeypatch, tmp_path):
    """Exports read ../smi/data; tests must not depend on what is in it."""
    import build_exports
    monkeypatch.setattr(build_exports, "SMI_INCIDENTS_CSV", tmp_path / "no_smi_incidents.csv")
    monkeypatch.setattr(build_exports, "SMI_REMOVED_CSV", tmp_path / "no_smi_removed.csv")
