"""Smoke test: package imports cleanly."""


def test_import() -> None:
    import kukiihome_memory

    assert kukiihome_memory.__version__ == "0.1.0"
