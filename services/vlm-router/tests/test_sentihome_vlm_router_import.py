"""Smoke test: package imports cleanly."""


def test_import() -> None:
    import sentihome_vlm_router

    assert sentihome_vlm_router.__version__ == "0.1.0"
