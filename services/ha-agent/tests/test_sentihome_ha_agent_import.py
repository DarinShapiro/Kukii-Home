"""Smoke test: package imports cleanly."""


def test_import() -> None:
    import sentihome_ha_agent

    assert sentihome_ha_agent.__version__ == "0.1.0"
