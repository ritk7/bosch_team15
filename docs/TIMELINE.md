# Hackathon timeline: Team No-15, "Lil bosch"

Ritwik Katta · Nandan Murali Nari · Akram Lateef · Tejas · Akhilesh K

Every build step below matches a commit on GitHub (`git log` on github.com/ritk7/bosch_team15). Times are Saturday 10 Oct
2026, local time. Planned steps are marked as plan.

## Day 1: Saturday

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
| plan 14:40–16:15 | Real-Docker check of the fix loop and the campaign, wow layer (living map, judge vs AI), polish, README. | |
| plan 16:15 | Merge to `main` only if tests and the Docker selftest pass; otherwise submit the `milestone-1` tag. | |
| plan 17:00 | Milestone 1 submission; shortlisting on the idea. | |

## Day 2: Sunday (plan)

| Time | Plan |
|---|---|
| Morning | Run the real hawkBit on a 24 GB M4 Pro: `python dev.py selftest --target hawkbit`, then a campaign and findings on Bosch's own server. |
| By 15:00 | Feature freeze. Clean run from a fresh clone. Observability overlay (Prometheus, Grafana, cAdvisor). |
| Afternoon | Three timed rehearsals of the 90-second demo, the 3-minute video, and the five deliverables submitted 30 minutes early. |

## How we used AI tools
- **Claude Code (Opus 5.5)** pair-programmed the build: backend, frontend, tests and Docker verification, with every step
  checked by tests and real runs before committing.
- **Inside the product**, Claude Haiku 4.5 (through our router) runs the planner, predictor, risk and fix agents. The local
  Llama 3.2 3B (Ollama) is configured as the free tier. Every agent has a rules fallback, and every call is in the cost ledger.
