"""Target adapters: everything that is specific to one app under test (see docs/TARGETS.md)."""
from __future__ import annotations

from .. import config
from .hawkbit import HawkbitTarget
from .voting import VotingTarget

TARGETS = {"voting": VotingTarget, "hawkbit": HawkbitTarget}


def load(name: str | None = None):
    name = name or config.TARGET
    if name not in TARGETS:
        raise ValueError(f"Unknown target '{name}'. Use one of: {', '.join(TARGETS)}")
    target_dir = config.TARGET_DIR if name == config.TARGET else (config.REPO / "targets" / name).resolve()
    return TARGETS[name](target_dir)
