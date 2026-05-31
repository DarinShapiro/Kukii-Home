"""Smoke test: package imports cleanly."""


def test_import() -> None:
    import kukiihome_core

    assert kukiihome_core.__version__ == "0.1.0"
