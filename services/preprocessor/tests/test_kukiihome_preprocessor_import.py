"""Smoke test: package imports cleanly."""


def test_import() -> None:
    import kukiihome_preprocessor

    assert kukiihome_preprocessor.__version__ == "0.1.0"
