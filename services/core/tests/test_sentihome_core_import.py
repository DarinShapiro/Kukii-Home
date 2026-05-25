"""Smoke test: package imports cleanly."""


def test_import() -> None:
    import sentihome_core

    assert sentihome_core.__version__ == "0.1.0"
