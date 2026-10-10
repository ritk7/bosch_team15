"""DockerRunner: acts on the real target through the `docker compose` CLI (no Docker SDK dependency).

Safety: every command is scoped with `-p <project> -f <compose file>`, and service names are allow-listed from the
compose file, so nothing outside the target project can be touched.
"""
from __future__ import annotations

import asyncio
import json
from http.cookiejar import CookieJar, DefaultCookiePolicy
import shutil
from pathlib import Path

import httpx

from .sim import Clock

COMPOSE_STATE = {"running": "running", "exited": "stopped", "dead": "stopped", "created": "stopped",
                 "paused": "paused", "restarting": "restarting", "removing": "stopped"}


def docker_bin() -> str | None:
    found = shutil.which("docker")
    if found:
        return found
    for p in (Path.home() / ".docker/bin/docker", Path("/usr/local/bin/docker"), Path("/opt/homebrew/bin/docker")):
        if p.exists():
            return str(p)
    return None


async def run_cmd(args: list[str], timeout: float = 60.0) -> tuple[int, str, str]:
    """Run a command and return (exit code, stdout, stderr). Replaced by a fake in tests."""
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", f"timed out after {timeout:.0f} s"
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


def parse_ps(text: str) -> list[dict]:
    """`docker compose ps --format json` prints a JSON array on some versions and NDJSON on others."""
    text = text.strip()
    if not text:
        return []
    if text.startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


class DockerClient:
    """The probe's view of the real voting app: POST / on the vote service, then check ids in Postgres and Redis."""

    def __init__(self, runner: "DockerRunner", base_url: str = "http://localhost:8080", timeout_s: float = 5.0):
        self.runner, self.base_url = runner, base_url
        # No cookie jar: vote sets a voter_id cookie in every response, and a shared jar would send the first unit's id
        # with every later vote, so Postgres would upsert them into one row. Each unit sends its own id instead.
        jar = CookieJar(policy=DefaultCookiePolicy(allowed_domains=[]))
        self.http = httpx.AsyncClient(timeout=timeout_s, cookies=httpx.Cookies(jar))

    async def send(self, unit_id: str) -> tuple[int | None, float]:
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            r = await self.http.post(self.base_url + "/", data={"vote": "ab"[int(unit_id.rsplit("-", 1)[-1]) % 2]},
                                     headers={"Cookie": f"voter_id={unit_id}"})
            return r.status_code, (loop.time() - started) * 1000
        except httpx.HTTPError:
            return None, (loop.time() - started) * 1000

    async def stored_ids(self, prefix: str) -> set[str] | None:
        code, out, _ = await self.runner.exec("db", ["psql", "-U", "postgres", "-At", "-c",
                                                     f"select id from votes where id like '{prefix}%'"])
        if code != 0:
            return None
        return {line.strip() for line in out.splitlines() if line.strip()}

    async def queued_ids(self, prefix: str) -> set[str] | None:
        code, out, _ = await self.runner.exec("redis", ["redis-cli", "--raw", "LRANGE", "votes", "0", "-1"])
        if code != 0:
            return None
        ids = set()
        for line in out.splitlines():
            try:
                vid = json.loads(line).get("voter_id", "")
            except (ValueError, AttributeError):
                continue
            if vid.startswith(prefix):
                ids.add(vid)
        return ids


class DockerRunner:
    kind = "docker"

    def __init__(self, target, project: str | None = None, executor=run_cmd):
        self.target = target
        self.clock = Clock(1.0)
        self.compose = target.compose_file()
        self.project = project or self.compose.parent.name
        self.exe = executor
        self.docker = docker_bin() or "docker"
        graph = target.graph()
        self.allowed = {n["id"] for n in graph["nodes"]}
        self.healthchecked = {n["id"] for n in graph["nodes"] if n["healthcheck"]}
        self.depended_on = {n["id"] for n in graph["nodes"] if n["fan_in"] > 0}
        self.connected: dict[str, bool] = {s: True for s in self.allowed}
        self.cpus: dict[str, float | None] = {s: None for s in self.allowed}
        self._client: DockerClient | None = None

    # ---- command helpers -----------------------------------------------------------------------------------
    def compose_args(self, *args: str) -> list[str]:
        base = [self.docker, "compose", "-p", self.project, "-f", str(self.compose)]
        overlay = self.target.overlay_file()
        if overlay.exists():
            base += ["-f", str(overlay)]
        return base + list(args)

    def _check(self, svc: str) -> None:
        if svc not in self.allowed:
            raise ValueError(f"'{svc}' is not a service of the target compose project")

    async def _compose(self, *args: str, timeout: float = 60.0) -> str:
        code, out, err = await self.exe(self.compose_args(*args), timeout)
        if code != 0:
            raise RuntimeError(f"docker compose {' '.join(args)} failed: {(err or out).strip()[:300]}")
        return out

    async def exec(self, svc: str, cmd: list[str]) -> tuple[int, str, str]:
        self._check(svc)
        return await self.exe(self.compose_args("exec", "-T", svc, *cmd), 10.0)

    async def _ps(self) -> list[dict]:
        code, out, _ = await self.exe(self.compose_args("ps", "-a", "--format", "json"), 20.0)
        return parse_ps(out) if code == 0 else []

    async def _container(self, svc: str) -> str:
        for row in await self._ps():
            if row.get("Service") == svc:
                return row.get("Name") or row.get("ID")
        raise RuntimeError(f"No container for service '{svc}'. Is the target running?")

    # ---- runner protocol -----------------------------------------------------------------------------------
    async def ready(self) -> tuple[bool, str]:
        code, _, err = await self.exe([self.docker, "info", "--format", "{{.ServerVersion}}"], 10.0)
        if code != 0:
            return False, f"Docker daemon not answering: {err.strip()[:120]}"
        rows = await self._ps()
        running = sum(r.get("State") == "running" for r in rows if r.get("Service") in self.allowed)
        return running > 0, f"Docker project '{self.project}': {running}/{len(self.allowed)} services running"

    async def services(self) -> list[dict]:
        rows = {r.get("Service"): r for r in await self._ps()}
        names = [rows[s].get("Name") for s in self.allowed if s in rows and rows[s].get("Name")]
        restarts: dict[str, int] = {}
        if names:
            code, out, _ = await self.exe([self.docker, "inspect", "--format", "{{.Name}} {{.RestartCount}}", *names], 10.0)
            if code == 0:
                for line in out.splitlines():
                    name, _, count = line.strip().lstrip("/").partition(" ")
                    restarts[name] = int(count or 0)
        out_list = []
        for svc in sorted(self.allowed):
            row = rows.get(svc)
            state = COMPOSE_STATE.get((row or {}).get("State", "exited"), "stopped")
            health_raw = (row or {}).get("Health", "")
            health = "healthy" if health_raw == "healthy" else "unhealthy" if health_raw in ("unhealthy", "starting") else "none"
            if state == "running" and not self.connected[svc]:
                state = "degraded"
            out_list.append({"id": svc, "state": state, "health": health,
                             "restarts": restarts.get((row or {}).get("Name", ""), 0), "cpu_pct": None, "mem_mb": None})
        return out_list

    async def snapshot(self) -> dict[str, dict]:
        rows = {r.get("Service"): r for r in await self._ps()}
        return {s: {"state": COMPOSE_STATE.get((rows.get(s) or {}).get("State", "exited"), "stopped"),
                    "connected": self.connected[s], "cpus": self.cpus[s]} for s in self.allowed}

    async def crash(self, svc: str) -> None:
        """Make the main process exit, as a real crash would. `docker kill` marks the container as manually stopped, so a
        restart policy would never fire (measured); a signal from inside the container does not. Processes that ignore
        the signal as PID 1 (e.g. vote's Flask dev server) fall back to `docker compose kill`."""
        self._check(svc)
        container = await self._container(svc)
        before = await self._started(container)
        await self.exe(self.compose_args("exec", "-T", svc, "sh", "-c", "kill -TERM 1"), 10.0)
        for _ in range(8):
            await asyncio.sleep(0.5)
            now = await self._started(container)
            if now != before:  # exited, or exited and already restarted by its policy
                return
        await self._compose("kill", "-s", "SIGKILL", svc)

    async def _started(self, container: str) -> str:
        code, out, _ = await self.exe([self.docker, "inspect", "-f", "{{.State.Status}} {{.State.StartedAt}}", container], 10.0)
        return out.strip() if code == 0 else ""

    async def start(self, svc: str) -> None:
        self._check(svc)
        await self._compose("start", svc)

    async def pause(self, svc: str) -> None:
        self._check(svc)
        await self._compose("pause", svc)

    async def unpause(self, svc: str) -> None:
        self._check(svc)
        await self._compose("unpause", svc)

    async def _networks(self, container: str) -> list[str]:
        code, out, _ = await self.exe([self.docker, "inspect", "--format",
                                       "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}", container], 10.0)
        return out.split() if code == 0 else []

    async def disconnect(self, svc: str) -> None:
        self._check(svc)
        container = await self._container(svc)
        for net in await self._networks(container):
            if net.startswith(f"{self.project}_"):  # only the project's own networks
                await self.exe([self.docker, "network", "disconnect", net, container], 10.0)
        self.connected[svc] = False

    async def connect(self, svc: str) -> None:
        self._check(svc)
        container = await self._container(svc)
        spec = (self.target.graph_services().get(svc) or {})
        nets = spec.get("networks") or ["default"]
        for net in (nets if isinstance(nets, list) else list(nets)):
            await self.exe([self.docker, "network", "connect", "--alias", svc, f"{self.project}_{net}", container], 10.0)
        self.connected[svc] = True

    async def set_cpus(self, svc: str, cpus: float | None) -> None:
        self._check(svc)
        container = await self._container(svc)
        code, _, err = await self.exe([self.docker, "update", "--cpus", str(cpus or 0), container], 10.0)
        if code != 0:
            raise RuntimeError(f"docker update failed: {err.strip()[:200]}")
        self.cpus[svc] = cpus

    async def _wait_up(self, svc: str, max_s: float = 25.0) -> None:
        """Wait until the service runs, and until it is healthy when other services depend on it."""
        waited = 0.0
        while waited < max_s:
            row = next((r for r in await self._ps() if r.get("Service") == svc), {})
            running = row.get("State") == "running"
            healthy = row.get("Health") in ("", None, "healthy") or svc not in self.depended_on
            if running and healthy:
                return
            await asyncio.sleep(0.5)
            waited += 0.5

    async def restore_all(self, original: dict[str, dict], order: list[str]) -> list[str]:
        from .runner import differs

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
            if want["state"] == "running":
                await self._wait_up(name)
        return restored

    async def up(self) -> str:
        await self._compose("up", "-d", "--build", timeout=900)
        return f"docker compose up -d done for project '{self.project}'"

    async def down(self) -> str:
        await self._compose("stop", timeout=120)
        return f"docker compose stop done for project '{self.project}'"

    def client(self):
        if self._client is None:
            self._client = self.target.docker_client(self)
        return self._client
