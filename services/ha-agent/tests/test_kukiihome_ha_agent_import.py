"""Smoke test: package imports cleanly."""


def test_import() -> None:
    import kukiihome_ha_agent

    assert kukiihome_ha_agent.__version__ == "0.1.0"
