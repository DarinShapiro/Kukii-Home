"""Smoke test: package imports cleanly."""


def test_import() -> None:
    import kukiihome_notify

    assert kukiihome_notify.__version__ == "0.1.0"
