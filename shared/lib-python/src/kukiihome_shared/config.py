"""Environment + YAML config loader.

Services declare a pydantic model describing their config; ``load_config``
populates it from environment variables (preferred) with optional defaults
from a YAML file.

Example::

    from pydantic import BaseModel
    from kukiihome_shared.config import load_config

    class CoreConfig(BaseModel):
        nats_url: str
        log_level: str = "INFO"

    config = load_config(CoreConfig, prefix="KUKIIHOME_CORE_")
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel


def load_config[T: BaseModel](
    model: type[T],
    *,
    prefix: str = "",
    yaml_path: str | Path | None = None,
) -> T:
    """Load a config model from environment variables (+ optional YAML file).

    Resolution order (later overrides earlier):
        1. Defaults declared on the pydantic model
        2. YAML file at ``yaml_path`` (if provided and exists)
        3. Environment variables with ``prefix``

    Env variable name = ``{prefix}{FIELD_NAME_UPPER}``. Example:
        prefix="KUKIIHOME_CORE_", field "nats_url" → ``KUKIIHOME_CORE_NATS_URL``

    Args:
        model: Pydantic ``BaseModel`` subclass describing the config shape.
        prefix: Env variable prefix.
        yaml_path: Optional YAML file with defaults.

    Returns:
        A validated instance of ``model``.
    """
    data: dict[str, object] = {}

    if yaml_path is not None:
        path = Path(yaml_path)
        if path.exists():
            with path.open() as fh:
                loaded = yaml.safe_load(fh) or {}
                if isinstance(loaded, dict):
                    data.update(loaded)

    for field_name in model.model_fields:
        env_key = f"{prefix}{field_name.upper()}"
        if env_key in os.environ:
            data[field_name] = os.environ[env_key]

    return model.model_validate(data)
