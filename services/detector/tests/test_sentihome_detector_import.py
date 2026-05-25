"""Smoke test: package imports cleanly."""


def test_import() -> None:
    import sentihome_detector

    assert sentihome_detector.__version__ == "0.1.0"
