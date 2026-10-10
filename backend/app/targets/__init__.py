"""Target adapters: everything that is specific to one app under test (see docs/TARGETS.md)."""
from __future__ import annotations

from .. import config
from .voting import VotingTarget


def load(name: str | None = None) -> VotingTarget:
    name = name or config.TARGET
    if name == "voting":
        return VotingTarget(config.TARGET_DIR)
    raise ValueError(f"Target '{name}' is not built yet. Use TARGET=voting.")
