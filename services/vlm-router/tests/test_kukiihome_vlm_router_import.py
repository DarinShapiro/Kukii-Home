"""Smoke test: package imports cleanly."""


def test_import() -> None:
    import kukiihome_vlm_router

    assert kukiihome_vlm_router.__version__ == "0.1.0"
