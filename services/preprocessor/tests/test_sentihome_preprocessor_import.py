"""Smoke test: package imports cleanly."""


def test_import() -> None:
    import sentihome_preprocessor

    assert sentihome_preprocessor.__version__ == "0.1.0"
