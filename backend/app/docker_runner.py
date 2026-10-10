"""DockerRunner: acts on the real target through the `docker compose` CLI (no Docker SDK dependency).

Safety: every command is scoped with `-p <project> -f <compose file>`, and service names are allow-listed from the
compose file, so nothing outside the target project can be touched.
"""
from __future__ import annotations

import asyncio
import json
import re
from http.cookiejar import CookieJar, DefaultCookiePolicy
import shutil
from pathlib import Path

import httpx

from .sim import Clock

HELPER_IMAGE = "alpine:3.20"  # only used to deliver SIGKILL to one process of the target project
NETEM_IMAGE = "nicolaka/netshoot:v0.13"  # tc/netem, run inside one target container's network namespace only
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
    except (asyncio.TimeoutError, asyncio.CancelledError) as err:
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        await proc.communicate()  # drain pipes; canceled CLI children must not outlive the kill switch
        if isinstance(err, asyncio.CancelledError):
            raise
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
        self._disconnected_networks: dict[str, set[str]] = {s: set() for s in self.allowed}
        self.cpus: dict[str, float | None] = {s: None for s in self.allowed}
        self.net: dict[str, str | None] = {s: None for s in self.allowed}
        self._client: DockerClient | None = None
        self._stats: dict[str, tuple[float, float]] = {}  # container name -> (cpu %, memory MiB), from docker stats
        self._stats_at = 0.0
        self._stats_task: asyncio.Task | None = None

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
        if code != 0:
            raise RuntimeError("Cannot inspect Docker project state; readiness/restoration cannot be verified")
        return parse_ps(out)

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
        running = {r.get("Service") for r in rows if r.get("Service") in self.allowed
                   and r.get("State") == "running" and r.get("Health") not in ("unhealthy", "starting")}
        return running == self.allowed, f"Docker project '{self.project}': {len(running)}/{len(self.allowed)} services ready"

    def _refresh_stats(self, names: list[str]) -> None:
        """docker stats takes ~3 s, so it runs in the background every few seconds and the UI reads the cache."""
        loop = asyncio.get_running_loop()
        if self._stats_task and not self._stats_task.done() or loop.time() - self._stats_at < 4 or not names:
            return

        async def go() -> None:
            code, out, _ = await self.exe([self.docker, "stats", "--no-stream", "--format", "{{json .}}", *names], 15.0)
            self._stats_at = loop.time()
            if code != 0:
                return
            for line in out.splitlines():
                try:
                    d = json.loads(line)
                    used = d["MemUsage"].split("/")[0].strip()
                    mib = float(used[:-3]) * {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024}.get(used[-3:], 1)
                    self._stats[d["Name"]] = (float(d["CPUPerc"].rstrip("%")), round(mib, 1))
                except (KeyError, ValueError, IndexError):
                    continue

        self._stats_task = asyncio.create_task(go())

    async def services(self) -> list[dict]:
        rows = {r.get("Service"): r for r in await self._ps()}
        names = [rows[s].get("Name") for s in self.allowed if s in rows and rows[s].get("Name")]
        self._refresh_stats([rows[s]["Name"] for s in self.allowed if (rows.get(s) or {}).get("State") == "running"])
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
            cpu, mem = self._stats.get((row or {}).get("Name", ""), (None, None)) if state == "running" else (None, None)
            out_list.append({"id": svc, "state": state, "health": health,
                             "restarts": restarts.get((row or {}).get("Name", ""), 0), "cpu_pct": cpu, "mem_mb": mem})
        return out_list

    async def snapshot(self) -> dict[str, dict]:
        rows = {r.get("Service"): r for r in await self._ps()}
        return {s: {"state": COMPOSE_STATE.get((rows.get(s) or {}).get("State", "exited"), "stopped"),
                    "connected": self.connected[s], "cpus": self.cpus[s], "net": self.net[s]} for s in self.allowed}

    async def crash(self, svc: str) -> None:
        """SIGKILL the container's main process, as an OOM kill or segfault would (exit 137).
        `docker kill` marks a container as manually stopped, so a restart policy would never fire, and SIGTERM lets
        Redis save its data first (both measured). So a short-lived helper in the host PID namespace sends SIGKILL to
        exactly this container's process id (read from docker inspect). Falls back to `docker compose kill`."""
        self._check(svc)
        container = await self._container(svc)
        code, out, _ = await self.exe([self.docker, "inspect", "-f", "{{.State.Pid}}", container], 10.0)
        pid = out.strip()
        if code == 0 and pid.isdigit() and int(pid) > 1:
            code, _, _ = await self.exe([self.docker, "run", "--rm", "--privileged", "--pid=host", HELPER_IMAGE,
                                         "kill", "-9", pid], 30.0)
            if code == 0:
                return
        await self._compose("kill", "-s", "SIGKILL", svc)

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
        if code != 0:
            raise RuntimeError(f"Cannot inspect networks for '{container}'")
        return out.split()

    async def disconnect(self, svc: str) -> None:
        self._check(svc)
        container = await self._container(svc)
        nets = [net for net in await self._networks(container) if net.startswith(f"{self.project}_")]
        if not nets:
            raise RuntimeError(f"No project-scoped network to disconnect for '{svc}'")
        for net in nets:
            code, _, err = await self.exe([self.docker, "network", "disconnect", net, container], 10.0)
            if code != 0:
                raise RuntimeError(f"Network disconnect failed for '{svc}': {err.strip()[:200]}")
            self._disconnected_networks[svc].add(net)
            self.connected[svc] = False  # partial failure must still be rolled back

    async def connect(self, svc: str) -> None:
        self._check(svc)
        container = await self._container(svc)
        spec = (self.target.graph_services().get(svc) or {})
        nets = spec.get("networks") or ["default"]
        names = self._disconnected_networks[svc] or {f"{self.project}_{net}" for net in nets}
        attached = set(await self._networks(container))
        for net in sorted(names):
            if net in attached:
                continue
            code, _, err = await self.exe([self.docker, "network", "connect", "--alias", svc, net, container], 10.0)
            if code != 0:
                raise RuntimeError(f"Network reconnect failed for '{svc}': {err.strip()[:200]}")
        self._disconnected_networks[svc].clear()
        self.connected[svc] = True

    async def set_cpus(self, svc: str, cpus: float | None) -> None:
        self._check(svc)
        container = await self._container(svc)
        if not cpus:
            # measured: `docker update --cpus 0` exits 0 but silently keeps the old limit. Lifting it to every CPU of
            # the Docker VM removes the limit in practice.
            code, out, _ = await self.exe([self.docker, "info", "--format", "{{.NCPU}}"], 10.0)
            limit = out.strip() if code == 0 and out.strip().isdigit() else "64"
        else:
            limit = str(cpus)
        code, _, err = await self.exe([self.docker, "update", "--cpus", limit, container], 10.0)
        if code != 0:
            raise RuntimeError(f"docker update failed: {err.strip()[:200]}")
        self.cpus[svc] = cpus

    async def set_net(self, svc: str, spec: str | None) -> None:
        """Slow or lossy network: tc netem on every interface of this one container (e.g. 'delay 500ms', 'loss 25%').
        The helper joins only that container's network namespace; None removes the rule."""
        self._check(svc)
        container = await self._container(svc)
        if spec and not re.fullmatch(r"(delay \d+ms ?)?(loss \d+%)?", spec.strip()):
            raise ValueError(f"Refusing netem spec '{spec}'")
        rule = f"tc qdisc replace dev $i root netem {spec}" if spec else "tc qdisc del dev $i root 2>/dev/null || true"
        script = f"for i in $(ls /sys/class/net | grep -v '^lo$'); do {rule}; done"
        code, _, err = await self.exe([self.docker, "run", "--rm", "--net", f"container:{container}", "--cap-add",
                                       "NET_ADMIN", NETEM_IMAGE, "sh", "-c", script], 60.0)
        if code != 0:
            raise RuntimeError(f"netem failed on {svc}: {err.strip()[:200]}")
        self.net[svc] = spec

    async def _wait_up(self, svc: str, max_s: float = 90.0) -> None:
        """Wait for running/healthy, including health checks scheduled at Docker's default 30 s interval.

        The old 25 s restore deadline expired before MySQL's first post-crash health check even when its process
        was already answering. Keep verification bounded, but allow multiple checks before reporting failure.
        """
        deadline, exited = asyncio.get_running_loop().time() + max_s, 0
        while asyncio.get_running_loop().time() < deadline:
            row = next((r for r in await self._ps() if r.get("Service") == svc), {})
            running = row.get("State") == "running"
            exited = exited + 1 if row.get("State") in ("exited", "dead") else 0
            if exited >= 3:
                raise RuntimeError(f"Service '{svc}' exited repeatedly while waiting for readiness")
            healthy = row.get("Health") in ("", None, "healthy")
            if running and healthy:
                return
            await asyncio.sleep(0.5)
        raise RuntimeError(f"Service '{svc}' did not become ready within {max_s:g} s")

    async def restore_all(self, original: dict[str, dict], order: list[str]) -> list[str]:
        from .runner import differs

        restored: list[str] = []
        failures: list[str] = []
        for name in order:
            want = original.get(name)
            if want is None or name not in self.allowed:
                continue
            try:
                now = (await self.snapshot())[name]
                if not differs(now, want):
                    if want["state"] == "running":
                        await self._wait_up(name)  # auto-restarted containers can still be unhealthy
                    continue
                if now["state"] == "paused" and want["state"] == "running":
                    await self.unpause(name)
                elif now["state"] in ("stopped", "restarting") and want["state"] == "running":
                    await self.start(name)
                if now["connected"] != want["connected"]:
                    await (self.connect(name) if want["connected"] else self.disconnect(name))
                if now.get("cpus") != want.get("cpus"):
                    await self.set_cpus(name, want.get("cpus"))
                if now.get("net") != want.get("net") and want["state"] == "running":
                    await self.set_net(name, want.get("net"))
                if want["state"] == "running":
                    await self._wait_up(name)
                restored.append(name)
            except Exception as err:
                failures.append(f"{name}: {err}")
                # One failed dependency must not prevent recovery attempts on the remaining services.
        if failures:
            raise RuntimeError("Restoration incomplete: " + "; ".join(failures))
        return restored

    async def load(self, svc: str, workers: int):
        """Real query flood: N parallel heavy queries in the database container, each capped at 4 s server-side."""
        self._check(svc)
        cmd = (self.target.db_load or {}).get("cmd")
        if not cmd:
            raise ValueError(f"No load command for {svc}")

        async def worker() -> None:
            while True:
                await self.exec(svc, cmd)

        tasks = [asyncio.create_task(worker()) for _ in range(workers)]

        async def stop() -> None:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        return stop

    async def wait_ready(self, order: list[str], max_s: float = 90.0) -> bool:
        for svc in order:
            await self._wait_up(svc, max_s)
        return True

    async def apply_overlay(self, services: list[str], reset: bool = False) -> None:
        """Recreate the services whose compose settings changed (the override file is included by compose_args)."""
        for svc in services:
            self._check(svc)
        await self._compose("up", "-d", "--no-build", *services, timeout=180)
        for svc in services:
            await self._wait_up(svc, 60)

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
