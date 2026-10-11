# Chaos Sentinel findings report: Bosch hawkBit update server

> Real Bosch hawkBit on Docker (Tejas's 24 GB M4 Pro), exported from Chaos Sentinel's Report tab on 11 Oct 2026. Only the
> two footer lines under "Assumptions and limits" were corrected (the export predates the footer fix); raw export and all
> evidence: `docs/evidence/hawkbit-2026-10-11/`. Findings in this session came from the rules fallback because the AI risk
> answer failed the number check; the fix advice was written by the small model.


Generated 2026-10-11 08:04 · runner: **Docker (real containers)** · 15 experiment runs · probe 2 devices/s

## Resilience score

**30 / 100**

| Item | Points | Note |
|---|---:|---|
| Crash hawkbit | -51.1 | worst case, counted in full: recovery not observed within the bounded window, needed the kill switch, 94.3% errors |
| Hang mysql | -4.8 | counted at 35%: needed the kill switch |
| Slow network on mysql (+500 ms) | -4.8 | counted at 35%: needed the kill switch |
| Cut mysql off the network | -4.8 | counted at 35%: needed the kill switch |
| Hang hawkbit | -4.2 | counted at 35%: needed the kill switch |
| 5 experiments without measurable harm | 0 | they do not raise the score |

Formula (backend/app/score.py): per experiment, penalties for silent data loss, no recovery, guard trip, secondary failure, slow recovery and errors, weighted by the target's fan-in; worst counted in full, others at 35%.

## Weaknesses, ranked

Written by: rules (rules engine). Evidence is copied from measured outcomes.

### 1. [critical] Recovery was not observed after “Crash hawkbit”

Service `hawkbit`, experiment `crash-hawkbit`, confidence high.

The probe did not establish five consecutive healthy devices within the bounded recovery window.

- crash-hawkbit: 70 sent, 4 acknowledged, 4 updated, 0 stuck
- errors 94.3%, p95 over the run 15959 ms, peak 3 s p95 17460 ms, recovery not observed (bounded recovery window)
- guard: Error rate 67% over the last 3 s reached the 60% limit

### 2. [high] “Hang mysql” breaches the safety limits

Service `mysql`, experiment `hang-mysql`, confidence high.

The measured run reached a safety threshold and triggered restoration; overall errors were 0.0%.

- hang-mysql: 85 sent, 85 acknowledged, 85 updated, 0 stuck
- errors 0.0%, p95 over the run 4471 ms, peak 3 s p95 6116 ms, recovery 5.0 s
- guard: p95 latency 6.1 s reached the 5.6 s limit

### 3. [high] “Cut mysql off the network” breaches the safety limits

Service `mysql`, experiment `network-cut-mysql`, confidence high.

The measured run reached a safety threshold and triggered restoration; overall errors were 0.0%.

- network-cut-mysql: 16 sent, 16 acknowledged, 16 updated, 0 stuck
- errors 0.0%, p95 over the run 4046 ms, peak 3 s p95 4046 ms, recovery 3.0 s
- guard: p95 latency 3.2 s reached the 3.0 s limit

### 4. [high] “Slow network on mysql (+500 ms)” breaches the safety limits

Service `mysql`, experiment `latency-mysql`, confidence high.

The measured run reached a safety threshold and triggered restoration; overall errors were 0.0%.

- latency-mysql: 26 sent, 26 acknowledged, 26 updated, 0 stuck
- errors 0.0%, p95 over the run 7250 ms, peak 3 s p95 7974 ms, recovery 8.0 s
- guard: p95 latency 5.8 s reached the 5.6 s limit

### 5. [high] “Hang hawkbit” breaches the safety limits

Service `hawkbit`, experiment `hang-hawkbit`, confidence high.

The measured run reached a safety threshold and triggered restoration; overall errors were 0.0%.

- hang-hawkbit: 17 sent, 17 acknowledged, 17 updated, 0 stuck
- errors 0.0%, p95 over the run 3618 ms, peak 3 s p95 3618 ms, recovery 2.5 s
- guard: p95 latency 3.1 s reached the 3.0 s limit

## Recommended fixes

### X1. Fail fast when mysql is unreachable (code, advice, est. +5.0 points)

```
# Implementation checklist, not a drop-in config
# Dependency: mysql; callers: hawkbit
# Bound connection, pool-acquisition and read waits in this application's client library.
# Return a controlled unavailable response; make retries bounded and idempotent.
# Validate settings against the deployed driver and framework version.
```

- Configure mysql client connection timeout and read timeout in hawkbit's database driver to 5-10 seconds; validate settings match your deployed driver and framework version.
- Wrap mysql queries in hawkbit with try-catch; on timeout or unavailable exception, return HTTP 503 with Retry-After header.
- Test with chaos scenario 'Cut mysql off the network' to confirm hawkbit fails fast and clients receive 503 instead of hanging.

## All runs

| Experiment | Label | Errors | Devices stuck | Recovery | Guard | Prediction off by |
|---|---|---:|---:|---|---|---|
| Cut rabbitmq off the network | before | 0.0% | 0 | 0.0 s | not needed | -85.0 pts, -20 devices, recovery -12.0 s |
| Hang mysql | before | 0.0% | 0 | 2.5 s | tripped: p95 latency 3.1 s reached the 3.0 s limit | -85.0 pts, -8 devices, recovery 0.0 s |
| Cut mysql off the network | before | 0.0% | 0 | 3.0 s | tripped: p95 latency 3.2 s reached the 3.0 s limit | -95.0 pts, -2 devices, recovery 2.5 s |
| Crash hawkbit | campaign ai | 94.3% | 0 | not observed (bounded recovery window) | tripped: Error rate 67% over the last 3 s reached the 60% limit | 9.3 pts, -8 devices, recovery unavailable |
| Hang hawkbit | campaign ai | 0.0% | 0 | 2.5 s | tripped: p95 latency 3.1 s reached the 3.0 s limit | -5.0 pts, 0 devices, recovery 2.0 s |
| Crash rabbitmq | campaign ai | 0.0% | 0 | 3.5 s | not needed | -85.0 pts, -8 devices, recovery -8.5 s |
| Crash mysql | campaign ai | 0.0% | 0 | 1.0 s | not needed | -95.0 pts, -10 devices, recovery -11.0 s |
| Traffic spike on hawkbit (6×) | campaign ai | 0.0% | 0 | 0.0 s | not needed | -8.0 pts, 0 devices, recovery -1.5 s |
| Hang hawkbit | before | 0.0% | 0 | 2.5 s | tripped: p95 latency 3.1 s reached the 3.0 s limit | 0.0 pts, 0 devices, recovery 0.0 s |
| Crash mysql | selftest | 1.4% | 0 | 1.0 s | not needed | — |
| Crash mysql | selftest | 0.0% | 0 | 1.0 s | tripped: p95 latency 3.2 s reached the 3.0 s limit | — |
| Crash mysql | before | 0.0% | 0 | 1.0 s | not needed | -45.0 pts, 0 devices, recovery -0.2 s |
| Slow network on mysql (+500 ms) | before | 0.0% | 0 | 8.0 s | tripped: p95 latency 5.8 s reached the 5.6 s limit | -45.0 pts, -1 devices, recovery 4.5 s |
| Cut rabbitmq, then crash mysql | before | 0.0% | 0 | 7.0 s | not needed | -45.0 pts, -2 devices, recovery 3.5 s |
| Hang mysql | before | 0.0% | 0 | 5.0 s | tripped: p95 latency 6.1 s reached the 5.6 s limit | -85.0 pts, -17 devices, recovery -7.0 s |

## AI planner against random order

Top 3 weaknesses on the twin: compound-mysql-hang-traffic-spike, hang-mysql, latency-mysql. Planner order found them in 14 runs; random order needs 14.0 on average over 200 shuffles (of 17 experiments).

## Digital twin rehearsal

17 experiments rehearsed on the twin in 10.9 s.

| Experiment | Predicted penalty | Errors | Lost | Recovery |
|---|---:|---:|---:|---|
| Device surge while mysql is frozen | 60.0 | 94.6% | 0 | not observed (bounded recovery window) |
| Hang mysql | 60.0 | 94.4% | 0 | not observed (bounded recovery window) |
| Slow network on mysql (+500 ms) | 60.0 | 95.8% | 0 | not observed (bounded recovery window) |
| Crash hawkbit | 28.2 | 80.4% | 0 | 20.4 s |
| Cut mysql off the network | 19.5 | 33.3% | 0 | 2.5 s |
| Crash mysql | 15.4 | 9.5% | 0 | 5.1 s |
| Cut rabbitmq, then crash mysql | 15.1 | 7.4% | 0 | 4.9 s |
| Hang hawkbit | 12.0 | 0.0% | 0 | 2.5 s |
| Crash rabbitmq | 0.0 | 0.0% | 0 | 0.0 s |
| Hang rabbitmq | 0.0 | 0.0% | 0 | 0.0 s |
| Cut rabbitmq off the network | 0.0 | 0.0% | 0 | 0.0 s |
| Slow network on rabbitmq (+500 ms) | 0.0 | 0.0% | 0 | 0.0 s |
| Lossy network on mysql (25% loss) | 0.0 | 0.0% | 0 | 0.0 s |
| Lossy network on rabbitmq (25% loss) | 0.0 | 0.0% | 0 | 0.0 s |
| Overload mysql (heavy queries + 0.5 CPU) | 0.0 | 0.0% | 0 | 0.0 s |
| Throttle mysql to 0.1 CPU | 0.0 | 0.0% | 0 | 0.0 s |
| Traffic spike on hawkbit (6×) | 0.0 | 0.0% | 0 | 0.0 s |

## Assumptions and limits

- The probe runs 2 simulated devices per second; each one is created, assigned an update, polls the device API, downloads the deployment and reports success.
- A device counts as updated when the management API shows it in_sync; any HTTP failure or timeout is an error.
- Twin behaviour (simulation) is a hypothesis from the stack's configuration until measured on Docker.
- Guard limits: error rate over 3 s ≥ 60%, p95 ≥ max(3 s, 10× the measured baseline) (5.6 s in the final runs; 3.0 s in earlier runs before the baseline was re-measured), 1 other service down (blast radius), or > 45 s.
- Not injected on purpose: full disk (Docker Desktop's disk is shared by every container on the machine). Network latency was injected (tc netem) in this session.
- Model costs come from the ledger (Cost tab); rupee figures use USD_INR from backend/.env.
