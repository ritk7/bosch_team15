"""Adapter for Eclipse hawkBit (Bosch's over-the-air software update server): monolith + MySQL + RabbitMQ.

A unit is one device taking one software update, end to end through hawkBit's real APIs:
  management API: create the target, assign the distribution set
  device API (DDI, TargetToken auth): poll, read deploymentBase, report "closed / success" feedback
"updated" = the server shows the target in_sync; an HTTP failure or timeout on any step = error.
API paths, the TargetToken scheme and the feedback body are taken from hawkBit's source and docs (targets/hawkbit-src);
they are checked against a running server by `python dev.py selftest --target hawkbit` (needs ~4 GB for Docker).
"""
from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .. import config, mapper
from ..sim import SELF_HEAL, Clock, Svc
from .voting import SimClient

EMBEDDED = Path(__file__).with_name("hawkbit.compose.yml")
TENANT = "DEFAULT"
MGMT_AUTH = ("admin", "admin")  # hawkBit's documented default; sandbox only


@dataclass
class SimHawkbit:
    """Twin of the hawkBit stack. Behaviour below is a hypothesis from the stack's configuration, to be measured:
    - hawkbit down: connection refused at once; it has `restart: always`, and the JVM needs ~20 s to answer again.
    - mysql down or frozen: requests wait on the connection pool (Hikari waits 30 s) -> the device's 5 s timeout fires.
    - rabbitmq down: the device HTTP API (DDI) does not use it, so updates continue (unverified; DMF devices would stall)."""

    clock: Clock
    policies: dict[str, str | None] = field(default_factory=dict)
    seed: int = 11

    def __post_init__(self) -> None:
        self.warmup = {"hawkbit": 20.0, "mysql": 6.0, "rabbitmq": 4.0}
        self.hang_result: tuple[int | None, float] = (500, 30.0)  # Hikari connectionTimeout, longer than the probe's
        self.inflight = 0
        self.svcs = {n: Svc(n, self.policies.get(n)) for n in self.warmup}
        self.synced: set[str] = set()
        self.wedged = False  # measured: after MySQL latency (and 1 of 2 crashes) the API stayed dead until restarted
        self.rng = random.Random(self.seed)
        self._t = self.clock.now()

    def reach(self, name: str, t: float) -> str:
        s = self.svcs[name]
        if not s.connected or s.state in ("stopped", "restarting"):
            return "down"
        if s.state == "paused":
            return "paused"
        return "up" if t >= s.started_at + self.warmup[name] else "down"

    def _slow(self, name: str) -> float:
        cpus = self.svcs[name].cpus
        return 1.0 if not cpus else max(1.0, 1.0 / cpus)

    def advance(self, t: float | None = None) -> None:
        t = self.clock.now() if t is None else t
        for s in self.svcs.values():
            if s.restart_at is not None and s.restart_at <= t:
                s.state, s.started_at, s.restart_at = "running", s.restart_at, None
                s.restarts += 1
        self._t = t

    def try_unit(self, device_id: str, _vote: str = "") -> tuple[str, int | None, float]:
        t = self.clock.now()
        self.advance(t)
        h = self.reach("hawkbit", t)
        if h == "down":
            return "done", None, 0.002
        ms_ = self.svcs["mysql"]
        if ms_.delay_s() >= 0.3 or ms_.state == "paused":
            self.wedged = True  # broken JDBC connections are kept in the pool
        if self.wedged and h == "up":
            return "wait", None, 0.0  # requests hang on dead pooled connections, then time out
        if h == "paused":
            return "wait", None, 0.0
        m = self.reach("mysql", t)
        if m == "down" and not self.svcs["mysql"].connected:
            return "done", 500, 0.01  # the name no longer resolves: the pool fails fast
        if m in ("down", "paused"):
            return "wait", None, 0.0
        load = 1 + max(0, self.inflight - 10) * 0.05
        latency = (0.05 + self.rng.random() * 0.03) * self._slow("hawkbit") * max(1.0, self._slow("mysql") / 3) * load
        ms = self.svcs["mysql"]
        latency += 8 * ms.delay_s()  # several queries per device update, each a round trip
        if ms.loss() and self.rng.random() < ms.loss():
            latency += 1.0
        self.synced.add(device_id)
        return "done", 200, latency

    def stored_ids(self, prefix: str) -> set[str] | None:
        self.advance()
        if self.reach("hawkbit", self._t) != "up" or self.reach("mysql", self._t) != "up":
            return None
        return {d for d in self.synced if d.startswith(prefix)}

    def queued_ids(self, prefix: str) -> set[str] | None:
        return set() if self.stored_ids(prefix) is not None else None

    # container actions (same contract as SimVoting)
    def _exit(self, name: str, t: float) -> None:
        s = self.svcs[name]
        if t - s.started_at > 10:
            s.backoff = 0.1
        s.state = "stopped"
        if s.restart_policy in SELF_HEAL:
            s.state, s.restart_at = "restarting", t + s.backoff
            s.backoff = min(s.backoff * 2, 8.0)

    def crash(self, name: str) -> None:
        self.advance()
        if self.svcs[name].state in ("running", "paused"):
            self._exit(name, self._t)

    def stop(self, name: str) -> None:
        self.advance()
        self.svcs[name].state, self.svcs[name].restart_at = "stopped", None

    def start(self, name: str) -> None:
        self.advance()
        s = self.svcs[name]
        if s.state in ("stopped", "restarting"):
            s.state, s.started_at, s.restart_at = "running", self._t, None
            if name == "hawkbit":
                self.wedged = False  # a restart gives it a fresh connection pool

    def pause(self, name: str) -> None:
        self.advance()
        if self.svcs[name].state == "running":
            self.svcs[name].state = "paused"

    def unpause(self, name: str) -> None:
        self.advance()
        if self.svcs[name].state == "paused":
            self.svcs[name].state = "running"

    def set_connected(self, name: str, connected: bool) -> None:
        self.advance()
        self.svcs[name].connected = connected

    def set_cpus(self, name: str, cpus: float | None) -> None:
        self.advance()
        self.svcs[name].cpus = cpus

    def is_up(self, name: str) -> bool:
        self.advance()
        return self.reach(name, self._t) == "up"


class HawkbitClient:
    """Real devices against a real hawkBit: management API to create and assign, DDI to poll and report success."""

    def __init__(self, base_url: str = "http://localhost:8080", timeout_s: float = 15.0):
        # hawkBit's amd64 image runs under emulation on Apple Silicon: allow slower answers than the voting app
        self.base = base_url
        self.http = httpx.AsyncClient(timeout=timeout_s)
        self.ds_id: int | None = None

    async def wait_ready(self, max_s: float = 240.0) -> None:
        """The JVM accepts connections long before the REST API answers; wait until the management API responds."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max_s
        last = ""
        async with httpx.AsyncClient(timeout=30.0) as http:
            while loop.time() < deadline:
                try:
                    r = await http.get(f"{self.base}/rest/v1/targets", params={"limit": 1}, auth=MGMT_AUTH)
                    if r.status_code == 200:
                        return
                    last = f"HTTP {r.status_code}"
                except httpx.HTTPError as err:
                    last = type(err).__name__
                await asyncio.sleep(3)
        raise RuntimeError(f"hawkBit management API not ready after {max_s:.0f} s ({last})")

    async def prepare(self) -> None:
        """Idempotent: enable TargetToken auth for devices, then create one software module and one distribution set."""
        if self.ds_id is not None:
            return
        await self.wait_ready()
        r = await self.http.put(f"{self.base}/rest/v1/system/configs/authentication.targettoken.enabled",
                                json={"value": True}, auth=MGMT_AUTH)
        r.raise_for_status()
        tag = f"cs{random.randint(1000, 9999)}"
        r = await self.http.post(f"{self.base}/rest/v1/softwaremodules", auth=MGMT_AUTH,
                                 json=[{"name": f"chaos-sentinel-{tag}", "version": "1.0", "type": "os"}])
        r.raise_for_status()
        sm_id = r.json()[0]["id"]
        r = await self.http.post(f"{self.base}/rest/v1/distributionsets", auth=MGMT_AUTH,
                                 json=[{"name": f"chaos-sentinel-{tag}", "version": "1.0", "type": "os",
                                        "modules": [{"id": sm_id}]}])
        r.raise_for_status()
        self.ds_id = r.json()[0]["id"]

    async def send(self, unit_id: str) -> tuple[int | None, float]:
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            await self.prepare()
            r = await self.http.post(f"{self.base}/rest/v1/targets", auth=MGMT_AUTH,
                                     json=[{"controllerId": unit_id, "name": unit_id}])
            if r.status_code >= 300:
                return r.status_code, (loop.time() - started) * 1000
            token = r.json()[0].get("securityToken", "")
            r = await self.http.post(f"{self.base}/rest/v1/distributionsets/{self.ds_id}/assignedTargets",
                                     auth=MGMT_AUTH, json=[{"id": unit_id}])
            if r.status_code >= 300:
                return r.status_code, (loop.time() - started) * 1000
            dev = {"Authorization": f"TargetToken {token}"}
            base = f"{self.base}/{TENANT}/controller/v1/{unit_id}"
            r = await self.http.get(base, headers=dev)
            if r.status_code >= 300:
                return r.status_code, (loop.time() - started) * 1000
            href = ((r.json().get("_links") or {}).get("deploymentBase") or {}).get("href", "")
            match = re.search(r"/deploymentBase/(\d+)", href)
            if not match:
                return 500, (loop.time() - started) * 1000  # assigned but no update offered
            action = match.group(1)
            r = await self.http.get(href, headers=dev)
            if r.status_code >= 300:
                return r.status_code, (loop.time() - started) * 1000
            r = await self.http.post(f"{base}/deploymentBase/{action}/feedback", headers=dev,
                                     json={"status": {"execution": "closed", "result": {"finished": "success"}}})
            return r.status_code, (loop.time() - started) * 1000
        except (httpx.HTTPError, ValueError, KeyError, IndexError):
            return None, (loop.time() - started) * 1000

    async def _targets(self, prefix: str) -> list[dict] | None:
        try:
            rows: list[dict] = []
            seen: set[str] = set()
            while True:
                r = await self.http.get(f"{self.base}/rest/v1/targets", auth=MGMT_AUTH,
                                        params={"q": f"controllerId=={prefix}*", "limit": 500, "offset": len(rows)})
                if r.status_code != 200:
                    return None
                body = r.json()
                page = body.get("content", [])
                ids = {t["controllerId"] for t in page}
                if ids & seen:
                    return None  # ignored pagination / changing page: do not claim truncated data proves loss
                rows.extend(page)
                seen.update(ids)
                total = body.get("total")
                if (total is not None and len(rows) >= total) or (total is None and len(page) < 500):
                    return rows
                if not page:
                    return None
        except (httpx.HTTPError, ValueError):
            return None

    async def observed_ids(self, prefix: str) -> tuple[set[str] | None, set[str] | None]:
        rows = await self._targets(prefix)
        if rows is None:
            return None, None
        return ({t["controllerId"] for t in rows if t.get("updateStatus") == "pending"},
                {t["controllerId"] for t in rows if t.get("updateStatus") == "in_sync"})

    async def stored_ids(self, prefix: str) -> set[str] | None:
        rows = await self._targets(prefix)
        return None if rows is None else {t["controllerId"] for t in rows if t.get("updateStatus") == "in_sync"}

    async def queued_ids(self, prefix: str) -> set[str] | None:
        rows = await self._targets(prefix)
        return None if rows is None else {t["controllerId"] for t in rows if t.get("updateStatus") == "pending"}


class HawkbitTarget:
    name = "hawkbit"
    title = "Bosch hawkBit update server"
    entry = "hawkbit"
    probe_rate = 2  # units/s by default; hawkBit's amd64 image under emulation errors at 5/s (measured)
    selftest_experiment = "crash-mysql"
    db_load = {"service": "mysql", "cmd": ["mysql", "-uroot", "-e",
                                           "SET SESSION max_execution_time = 4000; SELECT BENCHMARK(300000000, SHA2('x', 256))"]}
    unit = {"singular": "device", "plural": "devices", "stored": "updated", "lost": "stuck"}
    roles = {"hawkbit": "api", "mysql": "database", "rabbitmq": "queue"}
    extra_edges = [
        {"from": "hawkbit", "to": "mysql", "kind": "dataflow", "label": "stores targets, rollouts, action states"},
        {"from": "hawkbit", "to": "rabbitmq", "kind": "dataflow", "label": "device messaging (DMF) and events"},
    ]
    notes = {
        "hawkbit": ["Device API (DDI) and management API in one Spring Boot service (monolith).",
                    "Waits for MySQL's healthcheck at start, but not for RabbitMQ."],
        "mysql": ["Every target, rollout and action state lives here."],
        "rabbitmq": ["Carries the DMF device-messaging API; HTTP (DDI) devices do not use it."],
    }

    def __init__(self, target_dir: Path):
        self.target_dir = target_dir

    def compose_file(self) -> Path:
        local = self.target_dir / "docker-compose.yml"
        return local if local.exists() else EMBEDDED

    def state_dir(self) -> Path:
        """Where approved fixes and map corrections live: next to the compose file when the app is cloned, otherwise
        in the tool's own data folder (creating targets/<name> would make setup.sh think the app is already cloned)."""
        if (self.target_dir / "docker-compose.yml").exists():
            return self.target_dir
        d = config.CACHE_DIR / self.name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def overlay_file(self) -> Path:
        return self.state_dir() / "docker-compose.fixes.yml"

    def graph_services(self) -> dict[str, dict]:
        return mapper.load_services(self.compose_file(), self.overlay_file())

    def graph(self) -> dict:
        return mapper.build_graph(self.compose_file(), roles=self.roles,
                                  extra_edges=mapper.corrected_edges(self.state_dir(), self.extra_edges),
                                  notes=self.notes, overlay=self.overlay_file())

    def extra_experiments(self, graph: dict) -> list[dict]:
        return [{"id": "compound-rabbitmq-down-mysql-crash", "type": "compound", "target": "mysql",
                 "title": "Cut rabbitmq, then crash mysql",
                 "description": "Take the messaging bus away, then kill the database 4 s later, mid-rollout. "
                                "Do devices end up updated or stuck?",
                 "params": {"steps": "disconnect:rabbitmq,crash:mysql", "delay_s": 4}, "risk_hint": 1.0},
                {"id": "compound-mysql-hang-traffic-spike", "type": "compound", "target": "mysql",
                 "title": "Device surge while mysql is frozen",
                 "description": "Freeze MySQL, then send 6 times the normal device traffic 3 s later, like a "
                                "fleet-wide rollout hitting a stalled database.",
                 "params": {"steps": "pause:mysql,spike:6", "delay_s": 3}, "risk_hint": 0.85}]

    def make_sim(self, clock: Clock, policies: dict) -> SimHawkbit:
        return SimHawkbit(clock, policies)

    def sim_client(self, app, clock: Clock) -> SimClient:
        return SimClient(app, clock, self.entry)

    def docker_client(self, runner) -> HawkbitClient:
        return HawkbitClient()

    async def app_ready(self, runner) -> tuple[bool, str]:
        """Application-level readiness: the management API answers. Measured on real hawkBit: after a MySQL
        disruption all three containers were running while /rest/v1/targets kept timing out."""
        if runner.kind != "docker":
            app = runner.app
            ok = app.is_up("hawkbit") and app.is_up("mysql") and not app.wedged
            return ok, "simulated hawkBit API answers" if ok else "simulated hawkBit API stuck"
        try:
            async with httpx.AsyncClient(timeout=5.0) as http:
                r = await http.get("http://localhost:8080/rest/v1/targets", params={"limit": 1}, auth=MGMT_AUTH)
            return r.status_code == 200, f"management API HTTP {r.status_code}"
        except httpx.HTTPError as err:
            return False, f"management API not answering ({type(err).__name__})"

    async def prepare(self, runner) -> None:
        if runner.kind == "docker":
            await runner.client().prepare()

    def assumptions(self, rate: float) -> list[str]:
        return [
            f"The probe runs {rate:g} simulated devices per second; each one is created, assigned an update, polls the "
            "device API, downloads the deployment and reports success.",
            "A device counts as updated when the management API shows it in_sync; any HTTP failure or timeout is an error.",
            "Twin behaviour (simulation) is a hypothesis from the stack's configuration until measured on Docker.",
        ]
