# Chaos Sentinel: the complete story (for the deck, the pitch and the judges)

Team No-15 "Lil bosch": Ritwik Katta, Nandan Murali Nair, Akram Lateef, Tejas, Akhilesh K.
Bosch AI Hackathon, 10–11 Oct 2026 · Repo: github.com/ritk7/bosch_team15

---

## 1. The one-liner
**An AI that predicts what a failure will do, breaks your app safely behind a kill switch written in plain code, proves
every unit of work that silently disappeared, recommends the fix, and re-runs to prove the fix worked.**

Pitch in one breath: *"We ran Chaos Sentinel on Bosch's real hawkBit: 15 experiments, 867 real device updates. No update
was ever lost, but crash the hawkBit server and 94% of updates fail and it isn't back within 30 seconds, and half a second
of database delay makes updates 13 times slower. Our tool found, ranked and explained that automatically, behind a
kill switch written in plain code."*

**Main project: Eclipse hawkBit.** The voting app is the blueprint: we built and proved the method there, show briefly
that it works, and spend the demo on hawkBit.

## 2. The problem (Bosch's statement)
"Build an AI system that intelligently introduces controlled failures into a cloud-native application environment and
analyzes how the system responds to improve its resilience."
Chaos engineering by hand is slow and unfocused. Engineers guess what to break, watch dashboards, and miss the failures
that look healthy. Bosch also stressed **low cost and high ROI**.

## 3. Our answer: a closed loop that proves itself

```
 MAP ──► REHEARSE ──► PLAN ──► PREDICT ──► INJECT ──► MEASURE ──► RESTORE ──► EXPLAIN ──► FIX ──► RE-RUN
 compose  digital     AI picks  AI commits  behind a   every unit  always     ranked      engineer  score
 file     twin, all   riskiest  to numbers  kill       by id in    (even if   findings,   approves  goes up
          in seconds  + reason  first       switch     the DB      we crash)  score
```

Seven properties, each one a scoring story:
1. **Predict before inject.** The AI commits to errors %, units lost and recovery time *before* touching anything, and the
   UI shows predicted vs measured. Being wrong is fine; that's how we learn what the system really does.
2. **Business-level truth, not HTTP 200.** Every probe unit has a unique id that we look up in the database. That's how we
   catch **ghost votes**: acknowledged with 200 OK, then gone.
3. **Safety is code, never a model.** The guard trips on error rate, latency, blast radius or time. Restore runs in
   `finally`. An interrupted fault is undone on restart, and a final check repairs anything that fails later.
4. **Intelligent planning, measured.** A digital twin rehearses every experiment in seconds. The AI planner picks with
   reasons; AI vs random order is computed, not claimed.
5. **Closed loop.** An engineer approves a fix, it's applied as a compose override, the same experiment re-runs, and the
   score rises (29 → 56.5 on real Docker, voting blueprint).
6. **Cheap.** Rules first, smallest model second, a response cache, and a ledger that proves the cost. It works with no AI key at all.
7. **Fits a Bosch workflow.** Any compose file works. A CI gate fails a pipeline below a resilience score. It runs on
   Bosch's own hawkBit.

## 4. The apps we test
| Role | Target | What it is | Why |
|---|---|---|---|
| **Main project** | **Eclipse hawkBit (EPL-2.0, from Bosch Software Innovations)** | Bosch's over-the-air software update server for devices and vehicles: monolith + MySQL + RabbitMQ | **Most teams use the voting app; nobody else tests Bosch's own product.** Units become devices getting an update: "updated" or "stuck" |
| Blueprint | Docker example-voting-app (Apache-2.0) | 5 services: vote → redis (queue) → worker → db (Postgres) → result | Where the method was built and proven on an 8 GB laptop; shown briefly to prove the tool works |

## 5. Architecture

```
 compose file ─► Mapper ─► dependency graph (services, depends_on, hidden data flows, fan-in, SPOF, restart policy)
                               │
       Digital twin ◄──────────┤  simulation built from the app's source code, corrected by real measurements
                               ▼
 Planner agent ─► next experiment + reason ─► prediction: expected errors / units lost / recovery
                               ▼
 Injector (docker CLI, project-scoped) ─► fault ─► target app ◄── Probe: 5 units/s, each id checked in the DB
                               ▲                                     │
 Guard (plain code, every 0.5 s) ─ trips on error rate / p95 / blast radius / wall time ─► restore_all (finally)
                               ▼
 Risk agent ─► ranked findings (evidence = measured numbers)     Score (deterministic formula)
 Fix agent  ─► fixes ─► engineer approves ─► compose override ─► re-run ─► score before / after
                               ▼
 Findings report · architecture page · CI gate · cost ledger · /metrics → Prometheus → Grafana
```

| Component | File | Job |
|---|---|---|
| Mapper | `backend/app/mapper.py` | compose YAML → graph; adds data flows the compose file can't express; flags SPOF, no restart policy, memory-only data |
| Runners | `runner.py`, `docker_runner.py` | one interface: real Docker CLI or the simulation (`RUNNER=docker/sim/auto`) |
| Targets | `targets/voting.py`, `targets/hawkbit.py` | per-app workload, business check, twin, extra experiments |
| Probe | `probe.py` | sends units, classifies each: stored / waiting / error / **lost silently** / pending |
| Injectors | `injectors.py` | crash (true SIGKILL), hang, network cut, network delay and packet loss (tc netem), CPU throttle, DB overload (query flood + CPU limit), traffic spike, combined failures |
| Guard | `guard.py` | the kill switch; thresholds below |
| Engine | `campaign.py` | phases, safety files, campaign, twin, re-run, demo reset |
| AI agents | `agents.py`, `planner.py`, `analysis.py` | 3 agents: planner (plans and predicts), risk, fix |
| Score | `score.py` | deterministic resilience score |
| Fixes | `fixes.py` | writes the approved override file |
| Report / CI | `report.py`, `cli.py` | markdown deliverables; `gate --min-score 80` |
| UI | `frontend/src` | Live, Pipeline, Campaign, Findings, Report, Cost tabs |

### The AI agents (3)
Each agent is one call to a small model with a strict JSON check, plus a rules fallback that works alone. Every decision is logged in the cost ledger.

| Agent | Decides | Guardrail |
|---|---|---|
| Planner | ranks every experiment with a reason; picks the next one; chooses the fault window by risk; **predicts** errors %, units lost, recovery s before injecting | may only pick from the allow-listed catalog; may not quote numbers we didn't measure; predictions range-checked and shown next to the measured result; 15 s time limit, then rules |
| Risk | rewords findings so engineers understand them | ids fixed; any number must already be in the measured evidence |
| Fix | concrete steps for each fix | only compose restart policies can be applied, and only after approval |

Models: Claude Haiku 4.5 through our OpenAI-compatible router (small tier); Llama 3.2 3B via Ollama available as a free
local tier; deterministic rules when no model answers. Cached answers must pass today's check too.

### Safety controls
- Guard limits: error rate over 3 s ≥ 60%; p95 latency ≥ max(3 s, 10× the measured baseline); collateral services down
  (services not faulted on purpose: 2 for the voting app, 1 for 3-service hawkBit); or experiment > 45 s.
- When and how safely: a fault is injected only into a steady app (no errors, low latency for 2 s), and riskier
  experiments get shorter fault windows (6, 8 or 10 s).
- Scope: every command is `docker compose -p <project>`; service names are allow-listed from the compose file.
  Two short-lived helpers act on one container of our own project at a time: `alpine --pid=host` sends SIGKILL to that
  container's process; `netshoot` joins only that container's network namespace for netem. Full disk is deliberately
  not injected (Docker Desktop's VM disk is shared by every container on the machine).
- Restore in `finally`. The active fault is saved to disk and undone on the next start. A pre-flight repairs a broken app
  before a new fault. A post-run watch catches services that die later (secondary failures).
- Fixes need an engineer's approval and go in a separate file that can be deleted to undo.

### Resilience score (deterministic)
Per experiment, penalties for silent data loss (20–30), never recovering (25), needing the kill switch (12), a secondary
failure (10), slow recovery (0–8) and errors (0–15), weighted by how many services depend on the target. The worst counts
in full and the others at 35%, so adding harmless experiments can never raise the score. Same results, same score.

## 6. Why we made each decision
| Decision | Why |
|---|---|
| Predict before inject | Turns a demo into an experiment; the predicted-vs-measured reveal is memorable and shows the AI learning |
| Check ids in the DB, not HTTP status | HTTP 200 lied: the ghost votes were all "200 OK" |
| Guard in code, not AI | Safety must be deterministic and explainable; the AI suggests, code decides |
| Digital twin | Rehearse thousands of failures without touching the real app; makes "plan intelligently" measurable |
| Small model + rules + cache | Bosch asked for low cost; it works offline with zero key |
| Engineer approves fixes | Bosch's principle: AI augments, engineers decide |
| Voting app + hawkBit | Voting app is reliable on a laptop; hawkBit makes us unique and Bosch-relevant |
| Probe at 5 units/s (not 10) | The worker can only take ~10/s (one pop per 100 ms in its source), so at 10/s it could never drain after a fault |

## 7. What real Docker taught us (changes driven by measurements)
These are our best "engineering maturity" stories: we trusted measurements over assumptions.
1. **Our own bug, caught by validation.** The first real run stored 1 of 25 votes: the HTTP client reused one voter cookie.
   We fixed the probe so each vote carries its own id. Lesson: validate the measurement before trusting it.
2. **Votes hang, they don't fail.** With Redis dead, vote requests hang rather than returning 500, so the guard trips on
   **latency**, not errors. We corrected the twin.
3. **`docker kill` breaks restart policies, and SIGTERM lets Redis save its data.** Neither is a real crash. We now SIGKILL
   the container's process through a tiny helper, as chaos tools do, so restart policies still fire and data really is lost.
4. **Ghost votes are real.** Pause the worker, then crash Redis: 224 votes acknowledged, 24 silently lost.
5. **A secondary failure.** After Redis returns, the worker dies on reconnect and nothing restarts it. We added a final
   safety check that repairs and reports it.
6. **The first fix wasn't enough.** `restart` on Redis alone: Redis healed, but its fast restart killed the worker (score
   fell 84 → 40). The fix agent now covers dependent workers too. Then: **57 → 74**, no guard needed.
7. **`docker update --cpus 0` silently keeps the limit.** The database stayed at 0.1 CPU after a throttle experiment.
   We now lift the limit to all of Docker's CPUs, and a test pins the exact command.
8. **The AI quoted a wrong number** ("90% error rate", measured 0.9%). Planner answers are now rejected if they invent
   numbers, cached answers included.

## 8. Results (all measured)
| Result | Number |
|---|---|
| Selftest on real Docker | PASS on all 6 steps (app up, map, baseline 25/25 stored, fault, guard restored, recovery) |
| Ghost votes (real Docker) | 22–27 acknowledged votes lost silently per run; 0 HTTP errors in some runs |
| Fix proof (real Docker) | resilience score 29 → 56.5 (11 Oct; 57 → 74 on 10 Oct) after `restart: unless-stopped` on redis + worker |
| Digital twin | 15 experiments in ~7 s (voting), 11 in ~3 s (hawkBit) |
| AI planner vs random (twin) | voting: 3–7 runs vs ~12 to find the top 3 weaknesses; hawkBit: about equal (9 vs 9.1). Say this honestly |
| Automated tests | 135 backend tests + 33 end-to-end system checks (guard trips, every experiment restores, kill switch, startup restore, scope, API contract) |

## 9. Timeline (Saturday 10 Oct 2026)

### Milestone 1 (Day 1): running app, map, one safe failure with a working kill switch
| Time | What |
|---|---|
| 09:30 | Hackathon starts |
| 09:30–11:45 | Brief, design (the 7 properties), build contract, target research |
| 11:50 | Chose the voting app plus hawkBit; planned Milestone 1 |
| 12:17 | Public repo created with the starting kit |
| 12:39 | Milestone 1 in simulation: map, probe by id, crash, guard, kill switch, prediction, Live view |
| 12:50 | Connected Claude Haiku via our router; first real AI prediction logged |
| 13:03 | **Real Docker**: selftest passes; found and fixed the cookie bug; twin corrected (votes hang). Tagged `milestone-1` |
| ~13:00 | Judges' checkpoint: live demo |

### Milestone 2 (Day 2 scope, built Saturday afternoon): planner, multiple experiments, analysis, score, deliverables
| Time | What |
|---|---|
| 14:00 | Strategy: bring Bosch hawkBit forward; build everything in layers |
| 14:03 | Failure types: hang, network cut, CPU throttle, traffic spike, compound (ghost votes) |
| 14:06 | Bosch hawkBit target: real management and device API client, twin, target switch |
| 14:13 | True SIGKILL crash; ghost votes verified on Docker; secondary-failure safety check |
| 14:18 | Score, findings, fixes, approve and apply, re-run, demo reset |
| 14:29 | AI campaign planner, digital twin rehearsal, AI vs random |
| 14:32 | Findings report, architecture overview, CI gate, Campaign and Report tabs |
| 14:42 | Fix loop proven on real Docker: 57 → 74 |
| 14:47 | Living map (flowing units, pile-ups, blast radius), human vs AI guess |
| 14:50 | AI campaign on Docker; invented-number guard; state survives restarts |
| 14:57 | Merged to `main` after the full check and the Docker selftest |
| 15:08 | Observability: `/metrics` → Prometheus → Grafana; live CPU and memory per container |
| 17:00 | Milestone 1 submission |

### Sunday (plan)
Real hawkBit on a 24 GB M4 Pro (`python dev.py selftest --target hawkbit`), clean run from a fresh clone, 3-minute video,
team form, three rehearsals, submit 30 minutes early.

## 9b. Added on day 1 evening (after the shortlist)
AI ranked plan for every experiment; per-service resilience ranking; network delay and packet loss; real DB overload;
traffic spike while a service is down; blast radius counting collateral services only; guard latency relative to the
baseline; fault window by risk and injection only into a steady app; manual map correction; health in the baseline;
live findings after every run; twin-guided planning; cost ledger kept across restarts; AI calls time-boxed (predictions
now 3–4 s); stage mode for projectors; one-command `setup.sh`; architecture diagram (`docs/architecture.png`).

## 10. Bosch checklist coverage
| Bosch asks | Status |
|---|---|
| Map services, dependencies, data flows | ✅ automatic |
| Plan experiments intelligently | ✅ AI planner + twin + computed comparison |
| Inject with safety limits (error rate, latency, blast radius) | ✅ all three + wall time |
| Observe errors, slowdowns, recovery | ✅ + silent data loss + Grafana |
| Weaknesses, fixes, ranked resilience score | ✅ |
| Failure types | ✅ crash, slow/lossy network (delay, loss, cut), overloaded DB, failed dependency, traffic spike, all verified on real Docker; ❌ full disk (deliberate, safety) |
| Stretch: re-run after a fix / live dashboard / combined failures | ✅ ✅ ✅ |
| Deliverables | ✅ source + README, architecture overview, findings report · ⏳ video, team form |

## 10b. Real Bosch hawkBit findings (measured on Tejas's M4 Pro)
**Final validation, 11 Oct, latest build** (report `docs/sample-findings-report-hawkbit.md`, evidence `docs/evidence/hawkbit-2026-10-11`):
selftest 6/6 PASS; 15 real runs, 10 experiment types, 867 devices through hawkBit's real APIs; resilience score 30/100.

| Experiment (real) | Devices updated | Errors | p95 / peak | Recovery | Guard |
|---|---|---|---|---|---|
| **Crash hawkbit** | 4/70 acknowledged | **94.3%** | 16.0 s / 17.5 s | **not within 30 s** | tripped (errors 67%) |
| Slow network on mysql (+500 ms) | 26/26 | 0% | 7.3 s / 8.0 s | 8 s | tripped |
| Hang mysql | 85/85 | 0% | 4.5 s / 6.1 s | 5 s | tripped |
| Cut mysql off the network | 16/16 | 0% | 4.0 s | 3 s | tripped |
| Hang hawkbit | 17/17 | 0% | 3.6 s | 2.5 s | tripped |
| Crash mysql | 72/72 | 0% | 0.24 s / 1.7 s | 1 s | not needed |
| Cut rabbitmq, then crash mysql | 81/81 | 0% | 5.0 s | 7 s | not needed |
| Crash / cut rabbitmq, traffic spike 6× | all | 0% | — | 0–3.5 s | not needed |

- **Updates are durable:** 0 devices stuck in any run.
- **Critical: hawkBit itself is the weak point.** One replica and a slow Java start: crashed, it wasn't back within 30 s
  even with `restart: always`. Services ranked: hawkbit 44.7, mysql 76.5, rabbitmq 100.
- **MySQL trouble stalls updates** (0.56 s → 4–8 s) and the guard trips; RabbitMQ is off the critical path for updates.
- **Intermittent wedge (10 Oct, earlier build):** +500 ms on mysql: 96% of updates failed and hawkBit's API never recovered
  by itself while containers kept running; the same after one of two MySQL crashes; a restart fixed it. Not reproduced in
  the 11 Oct runs. Earlier session also created 718 devices (crash mysql 71/71 in 24 s; combined 99/99 in 14 s).
- Recommended fixes: fail fast on MySQL timeouts (bounded waits, 503 + Retry-After; from the fix agent), database
  connection validation and pool eviction, a health check that exercises the database, and more than one hawkBit replica.
- Honest notes: risk findings in the final session came from the rules fallback (the AI answer failed the number check);
  hawkBit's fixes are code advice, so the before/after re-run is proven on the voting blueprint (29 → 56.5).
- Tool changes driven by this run: application-level readiness (not just running containers), automatic repair and
  reporting of a wedged service, a rate-aware steady gate, hawkBit default 2 devices/s, fresh warm baselines.

## 11. Honest limits (say them before judges ask)
- Real hawkBit is built from its source and docs and tested on its twin; real-server verification runs on the 24 GB laptop.
- Not built: disk-full (deliberate: shared VM disk), Kubernetes.
- On hawkBit the AI planner doesn't beat random yet.
- The hawkBit wedge is intermittent: seen on 10 Oct, not reproduced in the 11 Oct validation. On hawkBit the only fix is code
  advice, so the before/after re-run is proven on the voting-app blueprint.
- cAdvisor can't see containers on our Docker Desktop version, so Grafana shows our own measurements instead.

## 12. Demo script (90 seconds, hawkBit first)
1. **Blueprint, 15 s.** Voting app on real Docker: crash redis, the guard trips and restores. "The tool works on any compose app; we proved it here."
2. Switch the target to **Bosch hawkBit**: 3 services mapped (hawkbit → mysql, rabbitmq). "Units are now devices receiving an OTA update."
3. mysql → **Slow network (+500 ms)**. The AI prediction appears. "It commits to numbers first."
4. **Confirm and inject**: watch the device grid (updated or stuck); the guard watches and restores.
5. Reveal: predicted vs measured. Tell the real-server results: "No device was lost, but updates took 13 times longer and the guard stepped in. The worst one: crash the hawkBit server itself and 94% of updates fail and it isn't back within 30 seconds."
6. Pipeline tab: "Three AI agents decide, predict, explain and fix; safety is plain code."
7. Findings → ranked weaknesses, score, recommended fix (connection validation + a database health check) → approve → re-run.
8. Close on cost: the Cost tab.

## 13. Slide deck outline (8–10 slides)
1. **Title**: Chaos Sentinel. "Break it on purpose. Prove what broke. Prove the fix."
2. **Problem**: manual chaos is slow and blind; HTTP 200 can lie (ghost votes).
3. **Solution**: the closed loop diagram (section 3).
4. **How it works**: architecture (section 5) in 3 boxes: Planner (plans + predicts) → Injector + Guard → Risk + Fix.
5. **Safety**: kill switch in code, scope, restore, approval.
6. **Live demo** (or video).
7. **Results**: hawkBit (15 real runs, 0 lost, crash hawkbit 94% errors, MySQL delay 13× slower), blueprint ghost votes and 29 → 56.5, twin in seconds, 135 tests + 33 system checks (sections 8, 10b).
8. **What real Docker taught us**: 3 of the stories in section 7.
9. **Bosch fit**: hawkBit, CI gate, low cost, runs on-prem, no data leaves the machine.
10. **What's next**: Kubernetes, network latency and disk-full, more targets, nightly CI runs.

## 14. Marketing messages
- **Headline:** "Your dashboards say healthy. Chaos Sentinel counts what you actually lost."
- **For engineers:** predicts, injects safely, explains with evidence, proves the fix.
- **For managers:** a resilience score per release; a CI gate that blocks risky deploys; costs rupees, not servers.
- **For Bosch:** tested on hawkBit, Bosch's OTA server; open-source models; runs on-prem.
- **Differentiators vs LitmusChaos, Chaos Mesh, Krkn-AI, Harness:** they inject failures. We predict before injecting,
  check business correctness (lost units by id), rank findings, apply an approved fix and prove it, with no cloud
  account and a cheap model.

## 15. Questions judges may ask (two-sentence answers)
1. **What if the AI picks something dangerous?** It can only choose from an allow-listed catalog scoped to our project's
   containers. The kill switch and restore are plain code.
2. **How do you avoid AI hallucinations?** Every number the AI writes must already be in the measured evidence, or the
   answer is rejected and the rules output is used. The evidence lines are copied from measurements.
3. **How accurate are predictions?** Shown live as predicted vs measured; wrong predictions show us how the system
   really behaves. On re-runs the planner uses what it measured.
4. **How is the score computed?** A deterministic formula in `score.py` with a visible breakdown; same results, same score.
5. **What does it cost?** Read it from the Cost tab: model calls in paise, compared with a large-model baseline. Rules
   and the cache handle many decisions for free.
6. **Can it run at Bosch?** Yes: on-prem, open-source models possible, any compose file, CI gate included.
7. **Kubernetes?** The runner is an interface; Docker Compose today, Kubernetes next. It's not built yet.
8. **What didn't you test?** Network latency and loss, disk-full, and real hawkBit until tomorrow's run.
9. **Why not auto-fix?** Bosch's principle: AI suggests, engineers decide. We generate and apply the change only after approval.
10. **How do you know the measurement is right?** A baseline must store 25 of 25 units first; the selftest prints
    PASS/FAIL per step; it caught our own probe bug.

## 16. How to run (short)
- Laptop, twin: `RUNNER=sim python dev.py` → http://localhost:5173
- Real voting app: clone it into `targets/voting`, `docker compose up -d --build`, then `RUNNER=auto python dev.py`
- Real hawkBit: `docs/HAWKBIT.md`
- Checks: `python dev.py check` · `python dev.py selftest [--target hawkbit]`
- CI gate: `cd backend && python -m app.cli gate --min-score 80`
