# Hackathon timeline: Team No-15, "Lil bosch"

Ritwik Katta · Nandan Murali Nair · Akram Lateef · Tejas · Akhilesh K

All times are **IST (UTC+05:30)**. Day 1 is Saturday, **10 October 2026**; Day 2 is Sunday, **11 October 2026**.
The original build entries and their commit references are retained from the team timeline; their timestamps have not been independently rechecked.
Nandan’s testing entries use the shared terminal output, screenshots and exported reports. Testing times are approximate reporting times, not exact execution timestamps.
Planned work is explicitly marked and is not recorded as completed.

## Day 1: Saturday, 10 October 2026

| Time | What we did | Evidence |
|---|---|---|
| 09:30 | Hackathon starts. Problem statement: AI-driven chaos engineering for cloud-native resilience. | |
| 09:30–11:45 | Read the brief and the judging criteria, chose the design (predict before inject, business-level checks, deterministic kill switch, closed fix loop, low cost), and wrote the build contract (API shapes) and the target notes. | `CLAUDE.md`, `docs/CONTRACT.md`, `docs/TARGETS.md`, `docs/DEMO.md` |
| 11:50 | Picked the sample apps: Docker's example-voting-app (5 services) as the base, Bosch's Eclipse hawkBit (over-the-air update server) as the Bosch-relevant target. Planned Milestone 1. | |
| 12:17 | Public repository created with the starting kit: model router, cost ledger, cache, cost gauge, design tokens. | commit `Chaos Sentinel skeleton` |
| 12:39 | **Milestone 1 in simulation**: dependency map from the compose file plus undeclared data flows, a probe that checks every unit by id in the database, crash experiment, a guard in plain code (error rate, p95, blast radius, wall time), kill switch, restore of an interrupted fault on startup, AI prediction before each run, Live view. The simulation follows the voting app's source code. | commit `Milestone 1` |
| 12:50 | Connected the small model tier (Claude Haiku 4.5 via our OpenAI-compatible router). The first real prediction was logged in the cost ledger. | |
| 13:03 | **Real Docker**: Docker runner and `python dev.py selftest`, which passes every step on the real app. The real run caught a probe bug (one cookie id reused for every vote) and showed that votes *hang* when Redis dies, so we corrected the simulation from that measurement. Tagged `milestone-1`. | commit `Docker runner and selftest`, tag `milestone-1` |
| 13:00–13:45 | Judges' checkpoint: live demo of map → prediction → real crash → guard → predicted vs measured. | |
| 14:00 | Strategy: most teams use the voting app, so we brought Bosch's hawkBit forward and built the full product in layers. | |
| 14:03 | **Failure types**: hang, network cut, CPU throttle (overloaded database), traffic spike, and the combined "pause worker, then crash redis" (ghost votes). | commit `Failure types` |
| 14:06 | **Bosch hawkBit target**: compose (monolith, MySQL, RabbitMQ), an adapter that drives hawkBit's real management API and device API (TargetToken auth, deploymentBase, feedback), a digital twin, and a target switch in the UI. | commit `Bosch hawkBit target` |
| 14:13 | Measured on Docker that `docker kill` stops restart policies from firing and that SIGTERM lets Redis save its data, so a crash is now a true SIGKILL. **Ghost votes verified on real Docker: 24 of 224 acknowledged votes lost silently.** Found a secondary failure (the worker dies on reconnect) and added a final safety check that repairs and reports it. | commit `Real crash = SIGKILL` |
| 14:18 | **Analysis**: deterministic resilience score with breakdown, ranked findings with measured evidence (the model may reword, never invent numbers), fixes, engineer approval, compose override, re-run, before/after score (25.8 → 66.4 on the twin). | commit `Score, findings and fixes` |
| 14:29 | **AI campaign planner** (adaptive, a reason for each pick) and **digital twin rehearsal** (15 experiments in 6 s). AI vs random computed on the twin: 3 runs vs 11.9 to find the top 3 weaknesses (voting app). | commit `AI campaign planner` |
| 14:32 | Findings report and architecture overview generated from real runs, **CI gate** (`python -m app.cli gate --min-score 80`), Campaign and Report tabs. | commit `Report (findings + architecture), CI gate` |
| 14:42 | **Fix loop proven on real Docker: score 57 → 74.** The first attempt revealed that a fast Redis restart kills the worker, so the fix agent now also covers dependent workers. The final safety check watches longer, and a pre-flight repairs anything left down. | commit `Fix loop proven on real Docker` |
| 14:47 | **Living map** (units flow along data flows and pile up in red at a broken service; blast radius), **human vs AI** guess before each run, secondary-failure notes in the reveal. | commit `Living map` |
| 14:50 | AI campaign on real Docker (3 picks by Claude Haiku). Model answers are now rejected if they quote numbers not in the measurements, cached answers included. Demo state survives restarts. | commits `Demo state survives restarts`, `Cached model answers` |
| 14:57 | Full check plus a real Docker selftest, then **merged to `main`**. | merge commit |
| 15:08 | **Observability**: Prometheus and Grafana overlay scraping our `/metrics`; real CPU and memory per container on the map. | commit `Observability` |
| plan 17:00 | Milestone 1 submission; shortlisting on the idea. | |

### Evening testing — Nandan Murali Nair

| Time (IST, approximate) | What we did | Evidence / result |
|---|---|---|
| 22:12–22:28 | Set up the application on Windows: checked Python, Node.js and Git; created a Python 3.13 virtual environment and installed backend dependencies. | Shared PowerShell output; dependency installation completed. |
| 22:47 | Checked the hawkBit simulation with a MySQL CPU-throttle experiment. | Shared UI results: 65 units sent, acknowledged and updated; 0 errors and 0 stuck units. Simulation only. |
| 22:55–22:59 | Moved from a downloaded ZIP to a Git clone so teammate updates could be pulled into the test workspace. Installed dependencies in `ChaosSentinel-git`. | Successful `git clone` and installation output. |
| 23:58–00:07 (ends Sunday) | Began updating and checking the application; the completed automated-check result was shared after midnight. | See the Sunday 00:07 entry below. |

## Day 2: Sunday, 11 October 2026

### Overnight and morning testing — Nandan Murali Nair

| Time (IST, approximate) | What we did | Evidence / result |
|---|---|---|
| 00:07 | Ran `python dev.py check`. | **131 backend tests passed; 5 frontend tests passed; TypeScript check and production build passed.** Backend tests took 90.59 s. One Starlette/httpx deprecation warning was reported; no test failures. |
| 00:11–00:12 | Checked the hawkBit simulation baseline and MySQL crash experiment, including automatic guard restoration. | Shared UI results: 19 sent, 17 acknowledged/updated, 2 errors (10.5%), 0 stuck; recovery 4.5 s. Guard tripped on latency; all 3 services restored. |
| 00:17 | Tested the manual kill switch during an experiment. | UI reported “Stopped by kill switch”; all 3 services were running afterward. Remaining waiting/pending counters were flagged for review, not confirmed as a fault-restoration failure. |
| 00:19 | Ran another experiment after the manual stop to check that testing could continue. | Completed-run count reached 2; all 3 services running. Result showed 19 sent, 17 updated, 2 errors, with no waiting, pending or stuck units. |
| 00:21 | Reviewed Findings, the resilience-score breakdown and fix recommendations. | Score 84 with a measured guard-threshold finding. The recommendation was advice-only; applying a fix and proving an improvement were not verified in this session. |
| 00:23–00:24 | Ran a four-experiment hawkBit campaign and digital-twin rehearsal. | Campaign completed; total completed runs reached 6 and all 3 services were running. Twin rehearsed 17 experiments in 9.6 s. Rules-based order needed 17 runs versus a random-order average of 13.5 across 200 shuffles; this run did not demonstrate a planner advantage. |
| 00:26 | Downloaded and reviewed the findings report and architecture overview. | `hawkbit-findings-report.md` and `architecture.md` exported successfully. Findings report covered 6 simulation runs and score 79. Flagged documentation/UI inconsistencies for teammate review. |
| 00:28–00:30 | Reviewed the cost ledger and prepared feedback for teammates. | 29 decisions, **0 model calls and ₹0 model spend**, all using the rules engine. The displayed ₹3.70 large-model comparison was hypothetical, not an incurred cost or independently verified saving. |
| 07:03–07:06 | Prepared to rerun the application and pulled the team's latest changes. | Shared Git output: 27 files changed, including `Pipeline.tsx`, `docs/ARCHITECTURE.md` and `docs/TEAM_FORM.md`. A fresh automated check and manual regression test after this update had not yet been confirmed. |

### Review items from this testing session

- Reconcile the blast-radius guard description in exported documentation with the threshold shown in the UI.
- Correct inconsistent agent counts/names in the architecture text.
- Distinguish simulation coverage from real-Docker verification for network latency and packet-loss experiments.
- Investigate the differing RabbitMQ recovery results between the campaign and twin rehearsal, including their settings.
- Review waiting/pending counters after a manual stop and the apparently empty ranked-plan area in the captured UI.
- Make the ledger's rules-only decisions and hypothetical model-cost comparison clear.

These are observations and review requests from the tested version, not claims that every item is a confirmed defect or remains present after the morning update. This session verified local simulation behavior; it did not verify real Docker execution, connected-model behavior, an applied-fix loop, or deployment readiness.

### Remaining Sunday plan — completion not yet confirmed

| Time (IST) | Planned work |
|---|---|
| Morning, after the update | Repeat `python dev.py check` and the manual regression checks on the updated version, including the Pipeline view and the review items above. |
| Morning | Run real hawkBit on a 24 GB M4 Pro: `python dev.py selftest --target hawkbit`, then a campaign and findings on Bosch's own server. |
| By 15:00 | Feature freeze. Clean run from a fresh clone. Verify the observability overlay (Prometheus, Grafana, cAdvisor). |
| Afternoon | Three timed rehearsals of the 90-second demo, the 3-minute video, and the five deliverables submitted 30 minutes early. |

## How we used AI tools
- **Claude Code (Opus 5.5)** pair-programmed the build: backend, frontend, tests and Docker verification, with every step
  checked by tests and real runs before committing.
- **Inside the product**, Claude Haiku 4.5 (through our router) runs the planner, predictor, risk and fix agents. The local
  Llama 3.2 3B (Ollama) is configured as the free tier. Every agent has a rules fallback, and every call is in the cost ledger.

- **Testing support:** Nandan used ChatGPT/Codex for setup guidance, interpreting test results and documenting review items. His recorded local test session used the product’s rules fallback, with no model calls.
