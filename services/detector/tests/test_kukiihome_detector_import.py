"""Smoke test: package imports cleanly."""


def test_import() -> None:
    import kukiihome_detector

    assert kukiihome_detector.__version__ == "0.1.0"
