# Chaos Sentinel findings report: voting app

Generated 2026-10-10 22:35 · runner: **Docker (real containers)** · 10 experiment runs · probe 5 votes/s

## Resilience score

**35 / 100**

Score history: before fix 0 → after fix 35

| Item | Points | Note |
|---|---:|---|
| Pause worker, then crash redis | -35.3 | worst case, counted in full: 23 acknowledged units lost, slow recovery 16.4 s |
| Crash db | -9.2 | counted at 35%: 1 acknowledged unit lost |
| Crash redis | -9.2 | counted at 35%: 1 acknowledged unit lost |
| Cut redis off the network | -5.5 | counted at 35%: slow recovery 10.2 s, needed the kill switch |
| Hang redis | -5.5 | counted at 35%: needed the kill switch |

Formula (backend/app/score.py): per experiment, penalties for silent data loss, no recovery, guard trip, secondary failure, slow recovery and errors, weighted by the target's fan-in; worst counted in full, others at 35%.

## Weaknesses, ranked

Written by: small (claude-haiku-4-5). Evidence is copied from measured outcomes.

### 1. [critical] 23 votes acknowledged but not persisted after worker pause and Redis crash

Service `redis`, experiment `compound-worker-hang-redis-crash`, confidence high.

Data loss with no error signal: clients receive HTTP 200 success, but 23 votes never reach storage. Standard HTTP health checks mask the problem; silent data loss only visible by comparing request IDs to stored records.

- compound-worker-hang-redis-crash: 104 sent, 104 acknowledged, 81 stored, 23 lost silently
- errors 0.0%, peak p95 1093 ms, recovery 16.4 s

### 2. [critical] 1 vote acknowledged but not persisted after Redis crash

Service `redis`, experiment `crash-redis`, confidence high.

Data loss with no error signal: client receives HTTP 200 success, but 1 vote never reaches storage. Standard HTTP health checks mask the problem; silent data loss only visible by comparing request IDs to stored records.

- crash-redis: 73 sent, 73 acknowledged, 72 stored, 1 lost silently
- errors 0.0%, peak p95 588 ms, recovery 8.4 s

### 3. [critical] 1 vote acknowledged but not persisted after database crash

Service `db`, experiment `crash-db`, confidence high.

Data loss with no error signal: client receives HTTP 200 success, but 1 vote never reaches storage. Standard HTTP health checks mask the problem; silent data loss only visible by comparing request IDs to stored records.

- crash-db: 67 sent, 67 acknowledged, 66 stored, 1 lost silently
- errors 0.0%, peak p95 56 ms, recovery 0.2 s

### 4. [high] Redis network partition causes p95 latency of 3968 ms, exceeding 3.0 s safety threshold

Service `redis`, experiment `network-cut-redis`, confidence high.

Latency degradation breaches safety limits without error reporting: p95 reaches 3968 ms (3.3 s over threshold) while error rate remains 0.0%. System degradation is invisible to monitoring unless latency percentiles are explicitly checked.

- network-cut-redis: 73 sent, 73 acknowledged, 73 stored, 0 lost silently
- errors 0.0%, peak p95 3968 ms, recovery 10.2 s
- guard: p95 latency 3.3 s reached the 3.0 s limit

### 5. [high] Redis hang causes p95 latency of 3047 ms, reaching 3.0 s safety threshold

Service `redis`, experiment `hang-redis`, confidence high.

Latency degradation reaches safety limits without error reporting: p95 hits 3047 ms (exactly at 3.0 s threshold) while error rate remains 0.0%. System is at the edge of acceptable performance with no error signal.

- hang-redis: 58 sent, 58 acknowledged, 58 stored, 0 lost silently
- errors 0.0%, peak p95 3047 ms, recovery 4.0 s
- guard: p95 latency 3.0 s reached the 3.0 s limit

## Recommended fixes

### X1. Add restart: unless-stopped to db, redis, worker (compose, applied, est. +24.0 points)

```yaml
services:
  db:
    restart: unless-stopped
  redis:
    restart: unless-stopped
  worker:
    restart: unless-stopped

```

- Add `restart: unless-stopped` policy to db, redis, and worker services in docker-compose.yml.
- Verify containers restart automatically after a crash by stopping one manually and confirming it comes back online within seconds.
- Test that the application resumes normal operation without manual intervention after a container restart.

### X2. Make the redis queue durable (append-only file) (config, advice, est. +24.4 points)

```yaml
services:
  redis:
    command: ["redis-server", "--appendonly", "yes", "--appendfsync", "always"]
    volumes:
      - redis-data:/data
volumes:
  redis-data:
```

- Enable Redis append-only file (AOF) by setting `appendonly yes` in redis.conf to persist writes to disk on every acknowledgment.
- Implement reliable-queue pattern: use LMOVE to move votes to a processing list before deletion, and only delete after the database write succeeds.
- Verify that votes survive a Redis crash by stopping Redis mid-operation and confirming queued votes are still present after restart.

### X3. Fail fast when redis is unreachable (code, advice, est. +5.0 points)

```
# vote/app.py
Redis(host="redis", socket_timeout=1, socket_connect_timeout=1,
      retry_on_timeout=True)  # then return HTTP 503 with Retry-After instead of hanging
```

- Set connect and read timeouts on Redis client to 500ms–1s (short enough to fail fast, long enough for normal latency).
- Return HTTP 503 with `Retry-After` header when Redis is unreachable, so clients retry instead of hanging.
- Test timeout behavior by simulating a Redis network partition and confirming requests return 503 within the timeout window.

## All runs

| Experiment | Label | Errors | Votes lost silently | Recovery | Guard | Prediction off by |
|---|---|---:|---:|---|---|---|
| Pause worker, then crash redis | campaign ai | 2.2% | 22 | never (30 s cap) | tripped: p95 latency 3.4 s reached the 3.0 s limit | 2.2 pts, 22 votes, 25.5 s |
| ↳ Secondary failure: worker failed after the fault was removed and stayed down until Chaos Sentinel restarted it. | | | | | | |
| Crash redis | campaign ai | 1.5% | 9 | 7.8 s | tripped: p95 latency 3.3 s reached the 3.0 s limit | -83.5 pts, -41 votes, -7.2 s |
| Hang redis | campaign ai | 0.0% | 0 | 4.4 s | tripped: p95 latency 3.1 s reached the 3.0 s limit | -78.0 pts, -45 votes, -8.1 s |
| Cut redis off the network | campaign ai | 0.5% | 0 | never (30 s cap) | tripped: p95 latency 3.6 s reached the 3.0 s limit | -44.5 pts, -50 votes, 29.5 s |
| ↳ Secondary failure: worker failed after the fault was removed and stayed down until Chaos Sentinel restarted it. | | | | | | |
| Crash db | campaign ai | 0.0% | 0 | never (30 s cap) | not needed | 0.0 pts, 0 votes, 8.0 s |
| Pause worker, then crash redis | after fix | 0.0% | 23 | 16.4 s | not needed | -2.0 pts, 11 votes, 6.9 s |
| Cut redis off the network | after fix | 0.0% | 0 | 10.2 s | tripped: p95 latency 3.3 s reached the 3.0 s limit | -95.0 pts, 0 votes, 9.7 s |
| Crash redis | after fix | 0.0% | 1 | 8.4 s | not needed | -95.0 pts, -11 votes, -3.1 s |
| Crash db | after fix | 0.0% | 1 | 0.2 s | not needed | -85.0 pts, -46 votes, -11.8 s |
| Hang redis | after fix | 0.0% | 0 | 4.0 s | tripped: p95 latency 3.0 s reached the 3.0 s limit | 0.0 pts, -15 votes, -6.5 s |

## AI planner against random order

Top 3 weaknesses on the twin: compound-worker-hang-redis-crash, crash-db, crash-redis. Planner order found them in 4 runs; random order needs 16.5 on average over 200 shuffles (of 21 experiments).

## Digital twin rehearsal

21 experiments rehearsed on the twin in 10.0 s.

| Experiment | Predicted penalty | Errors | Lost | Recovery |
|---|---:|---:|---:|---|
| Pause worker, then crash redis | 60.0 | 0.0% | 22 | never (30 s cap) |
| Crash redis | 43.2 | 0.0% | 6 | 5.5 s |
| Crash db | 33.7 | 0.0% | 1 | 24.3 s |
| Cut db off the network | 31.3 | 0.0% | 1 | 19.6 s |
| Traffic spike while the worker is down | 25.0 | 0.0% | 0 | never (30 s cap) |
| Traffic spike on vote (6×) | 25.0 | 0.0% | 0 | never (30 s cap) |
| Crash result | 25.0 | 0.0% | 0 | never (30 s cap) |
| Cut redis off the network | 22.7 | 36.4% | 0 | 2.1 s |
| Crash vote | 19.7 | 51.3% | 0 | 3.9 s |
| Hang redis | 15.6 | 0.0% | 0 | 4.1 s |
| Slow network on redis (+500 ms) | 15.6 | 0.0% | 0 | 3.4 s |
| Lossy network on redis (25% loss) | 15.6 | 0.0% | 0 | 3.1 s |
| Hang vote | 12.0 | 0.0% | 0 | 5.3 s |
| Hang db | 4.0 | 0.0% | 0 | 17.7 s |
| Slow network on db (+500 ms) | 4.0 | 0.0% | 0 | 17.6 s |
| Crash worker | 4.0 | 0.0% | 0 | 19.9 s |
| Throttle worker to 0.1 CPU | 3.0 | 0.0% | 0 | 17.4 s |
| Overload db (heavy queries + 0.5 CPU) | 2.9 | 0.0% | 0 | 15.6 s |
| Throttle db to 0.1 CPU | 2.8 | 0.0% | 0 | 15.4 s |
| Lossy network on db (25% loss) | 0.0 | 0.0% | 0 | 0.0 s |
| Hang worker | 0.0 | 0.0% | 0 | 9.2 s |

## Assumptions and limits

- The probe posts 5 votes per second, each with a unique voter_id (cs-<run>-<n>), and checks ids in Postgres.
- A vote is lost when it got HTTP 200 but, after the settle period, is neither in Postgres nor still queued in Redis.
- A vote counts as healthy when it is stored within 2 s of being sent; recovery is measured from the first unhealthy vote to the start of the final unbroken run of at least 5 healthy votes.
- Simulation behaviour is derived from the app's source code and is a hypothesis until measured on Docker.
- Guard limits: error rate over 3 s ≥ 60%, p95 ≥ 3 s, 3 services down, or 45 s wall time.
- Not tested: network latency/packet loss injection (needs a privileged netem helper on Docker Desktop), disk-full.
- Model costs come from the ledger (Cost tab); rupee figures use USD_INR from backend/.env.
