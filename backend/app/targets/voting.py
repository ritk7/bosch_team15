"""Adapter for Docker's example-voting-app (vote, redis, worker, db, result). Ground truth: docs/TARGETS.md and the app's source."""
from __future__ import annotations

from pathlib import Path

from .. import mapper
from ..sim import Clock, SimVoting

EMBEDDED = Path(__file__).with_name("voting.compose.yml")


class SimClient:
    """The probe's view of a simulated app: send a unit, then read which ids were stored or are still queued."""

    def __init__(self, app, clock: Clock, entry: str, timeout_s: float = 5.0):
        self.app, self.clock, self.entry, self.timeout_s = app, clock, entry, timeout_s

    async def send(self, unit_id: str) -> tuple[int | None, float]:
        started = self.clock.now()
        self.app.inflight += 1
        try:
            while True:
                kind, status, latency = self.app.try_unit(unit_id, "ab"[int(unit_id.rsplit("-", 1)[-1]) % 2])
                waited = self.clock.now() - started
                if kind == "done":
                    await self.clock.sleep(latency)
                    return status, (waited + latency) * 1000
                # a dependency hangs: the request waits until it answers or a timeout fires
                if self.app.reach(self.entry, self.clock.now()) == "up":
                    code, limit = self.app.hang_result
                    if waited >= min(limit, self.timeout_s):
                        return (code if limit <= self.timeout_s else None), waited * 1000
                elif waited >= self.timeout_s:
                    return None, waited * 1000  # the probe gives up
                await self.clock.sleep(0.05)
        finally:
            self.app.inflight -= 1

    async def stored_ids(self, prefix: str) -> set[str] | None:
        return self.app.stored_ids(prefix)

    async def queued_ids(self, prefix: str) -> set[str] | None:
        return self.app.queued_ids(prefix)


class VotingTarget:
    name = "voting"
    title = "voting app"
    entry = "vote"  # the probe sends units here
    unit = {"singular": "vote", "plural": "votes", "stored": "stored", "lost": "lost silently"}
    roles = {"vote": "web", "redis": "queue", "worker": "worker", "db": "database", "result": "web"}
    extra_edges = [
        {"from": "vote", "to": "redis", "kind": "dataflow", "label": "pushes votes"},
        {"from": "worker", "to": "redis", "kind": "dataflow", "label": "pops votes"},
        {"from": "worker", "to": "db", "kind": "dataflow", "label": "writes votes"},
        {"from": "result", "to": "db", "kind": "dataflow", "label": "reads counts"},
    ]
    notes = {
        "vote": ["Pushes to Redis with no error handling: Redis down means HTTP 500 (vote/app.py).",
                 "Healthcheck is GET /, which never touches Redis."],
        "worker": ["Pops one vote per ~100 ms, so it drains at most ~10 votes/s (worker/Program.cs).",
                   "A Redis or Postgres error escapes the main loop and the process exits."],
        "result": ["Reads vote counts from Postgres every second; not on the voting path."],
    }

    def __init__(self, target_dir: Path):
        self.target_dir = target_dir

    def compose_file(self) -> Path:
        cloned = self.target_dir / "docker-compose.yml"
        return cloned if cloned.exists() else EMBEDDED

    def overlay_file(self) -> Path:
        return self.target_dir / "docker-compose.fixes.yml"

    def graph_services(self) -> dict[str, dict]:
        return mapper.load_services(self.compose_file(), self.overlay_file())

    def graph(self) -> dict:
        return mapper.build_graph(self.compose_file(), roles=self.roles, extra_edges=self.extra_edges,
                                  notes=self.notes, overlay=self.overlay_file())

    def extra_experiments(self, graph: dict) -> list[dict]:
        if not {"worker", "redis"} <= {n["id"] for n in graph["nodes"]}:
            return []
        return [{"id": "compound-worker-hang-redis-crash", "type": "compound", "target": "redis",
                 "title": "Pause worker, then crash redis",
                 "description": "Freeze the worker so votes pile up in Redis, then kill Redis 4 s later. Votes were "
                                "acknowledged with HTTP 200; do they survive?",
                 "params": {"steps": "pause:worker,crash:redis", "delay_s": 4}, "risk_hint": 1.0}]

    def make_sim(self, clock: Clock, policies: dict) -> SimVoting:
        return SimVoting(clock, policies)

    def sim_client(self, app, clock: Clock) -> SimClient:
        return SimClient(app, clock, self.entry)

    def docker_client(self, runner):
        from ..docker_runner import DockerClient

        return DockerClient(runner)

    async def prepare(self, runner) -> None:
        return None

    def assumptions(self, rate: float) -> list[str]:
        return [
            f"The probe posts {rate:g} votes per second, each with a unique voter_id (cs-<run>-<n>), and checks ids in Postgres.",
            "A vote is lost when it got HTTP 200 but, after the settle period, is neither in Postgres nor still queued in Redis.",
            "A vote counts as healthy when it is stored within 2 s of being sent; recovery is measured from the first unhealthy vote "
            "to the start of the final unbroken run of at least 5 healthy votes.",
            "Simulation behaviour is derived from the app's source code and is a hypothesis until measured on Docker.",
        ]
