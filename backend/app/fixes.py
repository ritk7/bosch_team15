"""Writes approved compose-level fixes to docker-compose.fixes.yml (an override on top of the original file) and applies
them. Only deterministic, allow-listed changes are applied here: restart policies. Everything else stays advice."""
from __future__ import annotations

from pathlib import Path

import yaml

ALLOWED_RESTART = {"unless-stopped", "always", "on-failure"}


def write_overlay(path: Path, restart: dict[str, str], allowed: set[str]) -> str:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    data = data or {}
    services = data.setdefault("services", {})
    for svc, policy in restart.items():
        if svc not in allowed or policy not in ALLOWED_RESTART:
            raise ValueError(f"Refusing to write restart '{policy}' for '{svc}'")
        services.setdefault(svc, {})["restart"] = policy
    text = "# Written by Chaos Sentinel after an engineer approved the fixes. Delete this file to undo.\n" + \
        yaml.safe_dump(data, sort_keys=True)
    path.write_text(text, encoding="utf-8")
    return text


def clear_overlay(path: Path) -> None:
    path.unlink(missing_ok=True)
