"""Adapter for Docker's example-voting-app (vote, redis, worker, db, result). Ground truth: docs/TARGETS.md and the app's source."""
from __future__ import annotations

from pathlib import Path

from .. import mapper
from ..sim import Clock, SimVoting

EMBEDDED = Path(__file__).with_name("voting.compose.yml")


class SimClient:
    """The probe's view of the simulated app: POST a vote, then read which ids reached Postgres or sit in Redis."""

    def __init__(self, app: SimVoting, clock: Clock, timeout_s: float = 5.0):
        self.app, self.clock, self.timeout_s = app, clock, timeout_s

    async def send(self, unit_id: str) -> tuple[int | None, float]:
        started = self.clock.now()
        while True:
            kind, status, latency = self.app.try_vote(unit_id, "ab"[int(unit_id.rsplit("-", 1)[-1]) % 2])
            waited = self.clock.now() - started
            if kind == "done":
                await self.clock.sleep(latency)
                return status, (waited + latency) * 1000
            # a dependency is paused: the request hangs until it answers or a timeout fires
            limit = self.timeout_s
            if self.app.reach("vote", self.clock.now()) == "up":
                limit = min(limit, 5.0)  # vote's own Redis socket_timeout -> HTTP 500
                if waited >= limit:
                    return 500, waited * 1000
            elif waited >= limit:
                return None, waited * 1000  # the probe gives up
            await self.clock.sleep(0.05)

    async def stored_ids(self, prefix: str) -> set[str] | None:
        return self.app.stored_ids(prefix)

    async def queued_ids(self, prefix: str) -> set[str] | None:
        return self.app.queued_ids(prefix)


class VotingTarget:
    name = "voting"
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

    def assumptions(self, rate: float) -> list[str]:
        return [
            f"The probe posts {rate:g} votes per second, each with a unique voter_id (cs-<run>-<n>), and checks ids in Postgres.",
            "A vote is lost when it got HTTP 200 but, after the settle period, is neither in Postgres nor still queued in Redis.",
            "A vote counts as healthy when it is stored within 2 s of being sent; recovery is measured from the first unhealthy vote "
            "to the start of the final unbroken run of at least 5 healthy votes.",
            "Simulation behaviour is derived from the app's source code and is a hypothesis until measured on Docker.",
        ]
