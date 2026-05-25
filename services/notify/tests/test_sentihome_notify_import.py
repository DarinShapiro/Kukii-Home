"""Smoke test: package imports cleanly."""


def test_import() -> None:
    import sentihome_notify

    assert sentihome_notify.__version__ == "0.1.0"
