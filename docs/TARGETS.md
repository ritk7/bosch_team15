# Targets: the apps Chaos Sentinel tests

Set with `TARGET=voting|hawkbit|generic` (default `voting`) and `TARGET_DIR` (default `targets/voting`).
A target adapter (`backend/app/targets/<name>.py`) provides: compose file path, extra dataflow edges, role hints, a **workload** that
sends units of work at a steady rate, a **business check** that decides which units were really stored or completed, and any extra
experiments. Everything else (runner, guard, agents, score, UI) is target-agnostic. The UI labels come from the adapter's `unit`
(`{singular, plural, stored, lost}`, for example votes/"stored"/"lost silently" or devices/"updated"/"stuck").

```python
class Target(Protocol):
    name: str
    unit: dict                                  # labels for the UI
    def compose_file(self) -> Path: ...
    def extra_edges(self) -> list[GraphEdge]: ...   # dataflow the compose file does not declare
    async def prepare(self, runner) -> None: ...    # idempotent provisioning, may be a no-op
    def make_probe(self, runner, rate_per_s: int) -> Probe: ...
    def extra_experiments(self, graph) -> list[Experiment]: ...
```

## 1. voting (default; runs on an 8 GB M2)
Source: github.com/dockersamples/example-voting-app (Apache-2.0). Run: `docker compose up -d --build` inside `targets/voting`.
Ground truth, read from the repo's real code:
- **vote** (Flask, host port 8080): POST `/` with form `vote=a|b`, cookie `voter_id`; does `redis.rpush('votes', json)` with `socket_timeout=5`
  and no error handling, so Redis down means HTTP 500; Redis paused means the request hangs up to 5 s.
- **redis** (`redis:alpine`): holds the list `votes`; a kill or restart loses what is queued. Healthcheck script mounted from `./healthchecks`.
- **worker** (.NET): loops every ~100 ms, pops from Redis, upserts into Postgres; reconnects to Redis and the db by itself.
- **db** (`postgres:15-alpine`, user and password `postgres`): table `votes(id varchar unique, vote varchar)`; `id` is the `voter_id`. Named volume `db-data`.
- **result** (Node, host port 8081): reads Postgres, pushes counts over socket.io.
- compose: healthchecks on vote, redis, db; `depends_on: service_healthy`; **no `restart:` policy anywhere**; one replica of everything; the dev compose
  bind-mounts source code and builds `vote` with `target: dev`.
- Workload: POST a vote with cookie `voter_id=cs-<run>-<n>`, unique per vote, 10 per second. Unit states: `ok` = 200 and the id appears in Postgres;
  `queued` = 200 but not stored yet; `error` = 5xx or timeout; `lost` = 200 but still not stored after the settle period (silent data loss).
- Business check: `docker compose exec -T db psql -U postgres -At -c "select id from votes where id like 'cs-%'"` and compare ids.
- Undeclared dataflow edges to add (the compose file only declares depends_on): vote to redis, worker to redis, worker to db, result to db.

| Experiment id | Type, target | Hypothesis (expected; verify by running, never state as fact) |
|---|---|---|
| crash-redis | crash, redis | vote returns 500 immediately; queued units lost; no restart policy means no self-recovery until the guard restores |
| hang-redis | hang (pause), redis | vote requests stall up to 5 s, p95 rises, then 500s; no data loss from the pause itself |
| crash-db | crash, db | worker retries; vote still returns 200 (queue absorbs); units pile up in Redis; result stops updating; recovery after restore |
| crash-worker | crash, worker | vote returns 200 but nothing is stored; queue grows; units drain after restore |
| crash-vote | crash, vote | the web entry point disappears: 100% errors, no data loss |
| network-cut-redis | network_cut, redis | like crash-redis, without losing the container's memory |
| cpu-throttle-worker | cpu_throttle, worker | drain slows, units stay `queued` longer |
| spike-vote | traffic_spike, vote | latency rises; compare against baseline |
| compound-worker-hang-redis-crash | pause worker, then kill redis | votes keep returning 200, pile up in Redis, then vanish when Redis dies: silent loss of acknowledged units |

Expected findings (hypotheses): no restart policies, single replicas, Redis queue not persistent, vote has no retry or fallback when Redis is down, no circuit
breaker, the compound failure loses acknowledged votes. Fixes the tool can apply automatically via a compose override: `restart: unless-stopped`, healthchecks,
resource limits. Fixes it only advises: Redis AOF persistence, retry with backoff in vote, a replica of the vote service, a durable queue.

## 2. hawkbit (optional, Bosch-relevant, needs a 16 GB machine; build only after voting works)
Source: github.com/eclipse-hawkbit/hawkbit (EPL-2.0, originally from Bosch Software Innovations): the over-the-air software update server for devices and vehicles.
Compose files: `docker/mysql/docker-compose-micro-services-mysql.yml` (includes `docker-compose-deps-mysql.yml`): services `hawkbit-ddi` (device API, port 8081),
`hawkbit-dmf` (device messaging), `hawkbit-mgmt` (management API, port 8080), `mysql` (3306, empty root password, healthcheck), `rabbitmq` (5672, 15672, guest/guest, no healthcheck).
Images are amd64 only and `latest` tagged; the hawkBit services have `restart: always` and wait for MySQL's healthcheck but not for RabbitMQ. A shared `artifactrepo` volume is
used by mgmt and ddi.
Facts from the repo: default management user `admin:admin`; Management API under `/rest/v1/...` (targets, softwaremodules, distributionsets, rollouts, actions); Device (DDI) API under
`/{tenant}/controller/v1/{controllerId}` with the default tenant `DEFAULT`; device authentication (target token or gateway token) is disabled by default (see `docs/authentication.md` in the repo).
**Everything below is an unverified plan. Verify each call against the running server's API docs before relying on it.**
- `prepare()`: enable gateway-token device authentication through the system config API; create a software module, upload a small artifact, create a distribution set, create
  or assign a rollout to N simulated devices (targets can auto-register on first poll with a gateway token).
- Workload: N simulated devices (asyncio) poll `controller/v1/{id}`, follow `deploymentBase` when offered, download the artifact, post feedback (proceeding, then closed/success).
- Units are devices: `ok` = update finished successfully, `queued` = assigned or in progress, `error` = HTTP failure, `lost` = device reported success but the server still shows it
  unfinished, or stuck past the timeout. Business check: the management API's action status per device.
- Experiments of interest: stop RabbitMQ (service-to-service events), stop MySQL, kill `hawkbit-ddi` (devices retry by design, so impact may be small: measure, do not assume),
  fill or throttle the shared artifact volume, traffic spike of polling devices. Whether killing RabbitMQ stalls devices that use the HTTP device API is unknown.

## 3. generic (any compose file)
No business check. Probe the HTTP endpoints listed in `targets/generic.yaml` (`url`, `expect_status`) for availability and latency only; unit states reduce to `ok` and `error`.
The map, planner, guard, outcomes (errors, latency, recovery), score and report all still work.
