# Chaos Sentinel: the complete story (for the deck, the pitch and the judges)

Team No-15 "Lil bosch": Ritwik Katta, Nandan Murali Nari, Akram Lateef, Tejas, Akhilesh K.
Bosch AI Hackathon, 10–11 Oct 2026 · Repo: github.com/ritk7/bosch_team15

---

## 1. The one-liner
**An AI that predicts what a failure will do, breaks your app safely behind a kill switch written in plain code, proves
every unit of work that silently disappeared, recommends the fix, and re-runs to prove the fix worked.**

Pitch in one breath: *"Others break things and read logs. We predict the damage, break the app safely, count every lost
vote, fix it, and show the score go up, for a few rupees."*

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
   score rises (57 → 74 on real Docker).
6. **Cheap.** Rules first, smallest model second, a response cache, and a ledger that proves the cost. It works with no AI key at all.
7. **Fits a Bosch workflow.** Any compose file works. A CI gate fails a pipeline below a resilience score. It runs on
   Bosch's own hawkBit.

## 4. The apps we test
| Target | What it is | Why |
|---|---|---|
| Docker example-voting-app (Apache-2.0) | 5 services: vote (Flask) → redis (queue) → worker (.NET) → db (Postgres) → result (Node) | The classic multi-service app. Runs on an 8 GB laptop. Has a real queue, so data can be lost |
| **Eclipse hawkBit (EPL-2.0, from Bosch Software Innovations)** | Bosch's over-the-air software update server for devices and vehicles: monolith + MySQL + RabbitMQ | **Most teams use the voting app; nobody else tests Bosch's own product.** Units become devices getting an update: "updated" or "stuck" |

## 5. Architecture

```
 compose file ─► Mapper ─► dependency graph (services, depends_on, hidden data flows, fan-in, SPOF, restart policy)
                               │
       Digital twin ◄──────────┤  simulation built from the app's source code, corrected by real measurements
                               ▼
 Planner agent ─► next experiment + reason ─► Predictor agent ─► expected errors / units lost / recovery
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
| AI agents | `agents.py`, `planner.py`, `analysis.py` | planner, predictor, risk, fix |
| Score | `score.py` | deterministic resilience score |
| Fixes | `fixes.py` | writes the approved override file |
| Report / CI | `report.py`, `cli.py` | markdown deliverables; `gate --min-score 80` |
| UI | `frontend/src` | Live, Pipeline, Campaign, Findings, Report, Cost tabs |

### The AI agents (4)
Each agent is one call to a small model with a strict JSON check, plus a rules fallback that works alone. Every decision is logged in the cost ledger.

| Agent | Decides | Guardrail |
|---|---|---|
| Planner | ranks every experiment with a reason; picks the next one; chooses the fault window by risk | may only pick from the allow-listed catalog; may not quote numbers we didn't measure; 15 s time limit, then rules |
| Predictor | expected errors %, units lost, recovery s | numbers must be in range; shown next to the measured result |
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
| Fix proof (real Docker) | resilience score 57 → 74 after `restart: unless-stopped` on redis + worker |
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
- Our client drives hawkBit's real management API and device API: 718 devices created, the checked ones `in_sync`.
- Crash mysql: 71/71 devices updated, recovered in 24 s. Combined (cut rabbitmq, crash mysql): 99/99, 14 s.
- +500 ms latency on mysql: 96% of updates failed and **hawkBit's API never recovered by itself**; containers kept
  running. The same happened after one of two MySQL crashes. A restart of hawkBit fixed it.
- So: hawkBit does not reliably re-establish its database connections after a disruption. That's our headline Bosch
  finding. The fix to recommend: connection validation and pool eviction (e.g. Hikari keepalive / validation timeout),
  plus a health check that exercises the database, so Docker can restart it automatically.
- Tool changes driven by this run: application-level readiness (not just running containers), automatic repair and
  reporting of a wedged service, a rate-aware steady gate, hawkBit default 2 devices/s, fresh warm baselines.

## 11. Honest limits (say them before judges ask)
- Real hawkBit is built from its source and docs and tested on its twin; real-server verification runs on the 24 GB laptop.
- Not built: disk-full (deliberate: shared VM disk), Kubernetes.
- On hawkBit the AI planner doesn't beat random yet.
- cAdvisor can't see containers on our Docker Desktop version, so Grafana shows our own measurements instead.

## 12. Demo script (90 seconds)
1. Live tab: the map with 5 green services, the Docker badge. "Mapped automatically, including hidden data flows."
2. redis → **Pause worker, then crash redis**. The AI prediction appears. "It commits to numbers first." Judge enters a guess.
3. **Confirm and inject**: worker frozen, redis dies, red units pile up, blast-radius outlines, amber grid.
4. The guard trips and restores. Reveal: predicted vs measured vs the judge. "Every vote got 200 OK. About 25 vanished."
5. Pipeline tab: "Here is every stage that just ran: three AI agents, plain-code safety, the engineer approves." Wires show the flow.
6. Findings → Analyse → ranked weaknesses and score → approve the restart fix → Re-run → score rises.
7. Campaign → Rehearse on the twin: heatmap in seconds; AI vs random.
8. Switch the target to **Bosch hawkBit**: same tool, Bosch's own OTA server.
9. Close on cost: read the rupee figure from the Cost tab.

## 13. Slide deck outline (8–10 slides)
1. **Title**: Chaos Sentinel. "Break it on purpose. Prove what broke. Prove the fix."
2. **Problem**: manual chaos is slow and blind; HTTP 200 can lie (ghost votes).
3. **Solution**: the closed loop diagram (section 3).
4. **How it works**: architecture (section 5) in 3 boxes: Planner + Predictor → Injector + Guard → Analyst + Fix.
5. **Safety**: kill switch in code, scope, restore, approval.
6. **Live demo** (or video).
7. **Results**: ghost votes, 57 → 74, twin in 7 s, 3 vs 12 runs, 19 tests (section 8).
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
   really behaves. On re-runs the predictor uses what it measured.
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
