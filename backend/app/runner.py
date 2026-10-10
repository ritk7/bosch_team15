"""Runners act on the target's containers. Every action is allow-listed to the services of the target compose file.

SimRunner drives the in-memory simulation. DockerRunner (real `docker compose` CLI) is built in Milestone 2.
"""
from __future__ import annotations

from typing import Protocol

from . import config
from .sim import WARMUP_S, Clock, SimVoting


class Runner(Protocol):
    kind: str
    clock: Clock

    async def ready(self) -> tuple[bool, str]: ...
    async def services(self) -> list[dict]: ...
    async def snapshot(self) -> dict[str, dict]: ...
    async def crash(self, svc: str) -> None: ...
    async def start(self, svc: str) -> None: ...
    async def pause(self, svc: str) -> None: ...
    async def unpause(self, svc: str) -> None: ...
    async def disconnect(self, svc: str) -> None: ...
    async def connect(self, svc: str) -> None: ...
    async def set_cpus(self, svc: str, cpus: float | None) -> None: ...
    async def restore_all(self, original: dict[str, dict], order: list[str]) -> list[str]: ...
    async def up(self) -> str: ...
    async def down(self) -> str: ...
    def client(self): ...


def differs(now: dict, want: dict) -> bool:
    return (now["state"] != want["state"] or now["connected"] != want["connected"]
            or now.get("cpus") != want.get("cpus"))


class SimRunner:
    kind = "sim"

    def __init__(self, target, clock: Clock | None = None):
        self.target = target
        self.clock = clock or Clock(config.SIM_SPEED)
        self.allowed: set[str] = set()
        self.reset()

    def reset(self) -> None:
        graph = self.target.graph()
        self.allowed = {n["id"] for n in graph["nodes"]}
        self.app = SimVoting(self.clock, {n["id"]: n["restart"] for n in graph["nodes"]})

    def _check(self, svc: str) -> None:
        if svc not in self.allowed:
            raise ValueError(f"'{svc}' is not a service of the target compose project")

    async def ready(self) -> tuple[bool, str]:
        return True, "Simulation of the voting app (no Docker needed)"

    async def services(self) -> list[dict]:
        self.app.advance()
        t, out = self.clock.now(), []
        healthchecked = {n["id"] for n in self.target.graph()["nodes"] if n["healthcheck"]}
        for name in sorted(self.allowed):
            s = self.app.svcs[name]
            state = s.state
            health = "none"
            if state == "running" and name in healthchecked:
                health = "healthy" if self.app.reach(name, t) == "up" or not s.connected else "unhealthy"
            if state == "running" and not s.connected:
                state = "degraded"
            out.append({"id": name, "state": state, "health": health, "restarts": s.restarts,
                        "cpu_pct": None, "mem_mb": None})
        return out

    async def snapshot(self) -> dict[str, dict]:
        self.app.advance()
        return {n: {"state": "running" if s.state == "restarting" else s.state, "connected": s.connected, "cpus": s.cpus}
                for n, s in self.app.svcs.items() if n in self.allowed}

    async def crash(self, svc: str) -> None:
        self._check(svc)
        self.app.crash(svc)

    async def start(self, svc: str) -> None:
        self._check(svc)
        self.app.start(svc)

    async def pause(self, svc: str) -> None:
        self._check(svc)
        self.app.pause(svc)

    async def unpause(self, svc: str) -> None:
        self._check(svc)
        self.app.unpause(svc)

    async def disconnect(self, svc: str) -> None:
        self._check(svc)
        self.app.set_connected(svc, False)

    async def connect(self, svc: str) -> None:
        self._check(svc)
        self.app.set_connected(svc, True)

    async def set_cpus(self, svc: str, cpus: float | None) -> None:
        self._check(svc)
        self.app.set_cpus(svc, cpus)

    async def restore_all(self, original: dict[str, dict], order: list[str]) -> list[str]:
        """Bring every service back to its recorded state, dependencies first, and wait until they answer."""
        restored: list[str] = []
        for name in order:
            want = original.get(name)
            if want is None or name not in self.allowed:
                continue
            now = (await self.snapshot())[name]
            if not differs(now, want):
                continue
            if now["connected"] != want["connected"]:
                await (self.connect(name) if want["connected"] else self.disconnect(name))
            if now.get("cpus") != want.get("cpus"):
                await self.set_cpus(name, want.get("cpus"))
            if now["state"] == "paused" and want["state"] == "running":
                await self.unpause(name)
            elif now["state"] in ("stopped", "restarting") and want["state"] == "running":
                await self.start(name)
            restored.append(name)
            # wait for this service before starting the ones that need it (as compose does with service_healthy)
            for _ in range(int(WARMUP_S.get(name, 1.0) / 0.1) + 20):
                if want["state"] != "running" or self.app.is_up(name):
                    break
                await self.clock.sleep(0.1)
        return restored

    async def up(self) -> str:
        self.reset()
        return "Simulation reset: all services running"

    async def down(self) -> str:
        for name in self.allowed:
            self.app.stop(name)
        return "Simulation stopped: all services down"

    def client(self):
        from .targets.voting import SimClient

        return SimClient(self.app, self.clock)


def docker_available(target) -> bool:
    """auto mode: use Docker when the daemon answers and the target's compose project is cloned."""
    import subprocess

    from .docker_runner import docker_bin

    exe = docker_bin()
    if not exe or not (target.target_dir / "docker-compose.yml").exists():
        return False
    try:
        return subprocess.run([exe, "info"], capture_output=True, timeout=8).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def make_runner(target) -> Runner:
    kind = config.RUNNER
    if kind not in ("sim", "docker", "auto"):
        raise RuntimeError(f"Unknown RUNNER '{kind}'. Use sim, docker or auto.")
    if kind == "docker" or (kind == "auto" and docker_available(target)):
        from .docker_runner import DockerRunner

        return DockerRunner(target)
    return SimRunner(target)
