"""Smoke test: package imports cleanly."""


def test_import() -> None:
    import sentihome_memory

    assert sentihome_memory.__version__ == "0.1.0"
