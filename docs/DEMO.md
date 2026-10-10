# Demo, pitch and checklists

## Schedule that matters
- Sat 13:00 judges' check. Show: running app, dependency map, one failure with a working kill switch, and where it is going. Honest labelling: if only the simulation runs, say "Simulation".
- Sat 18:00 top-10 cut. The demo and a 2-minute pitch must be ready by 17:30: the judge-click flow with prediction and the predicted-vs-actual reveal, one finding, one fix.
- Sun: real Docker run, more failure types, before/after score, AI-vs-random comparison, report, CI gate. Feature freeze 15:00. Three timed rehearsals. Demo video of at most 3 minutes.
- Mon: interview. Everyone must be able to explain the whole system, not only their part.

## The judge-click demo (90 seconds)
1. Screen: dependency map (5 services, all green), the 100-cell grid in grey, cost gauge showing the spend so far.
2. Say: "This AI stress-tests an app by breaking it on purpose. Pick any service."
3. The judge clicks `redis`, chooses "Crash". The AI prediction card appears first: "I expect about N% errors, M votes lost, recovery S s, confidence X, because the vote service has no
   fallback when Redis is down." Say: "It commits to a number before touching anything."
4. Judge presses "Confirm and inject". The container dies, the node turns red, the grid fills with red cells, the chart climbs toward the guard line.
5. The guard trips (or the fault window ends): "The kill switch is plain code, not the model." The service is restored, the grid turns green.
6. Reveal predicted vs actual side by side, with the difference. Say what the AI learned and what it will test next.
7. Findings: ranked, with evidence ("votes acknowledged but never stored"). Fix: `restart: unless-stopped`. Press "Approve and apply", re-run the same experiment: it recovers by itself, score rises.
8. Close on cost: "The whole campaign cost ₹X. On the large model it would have been ₹Y." (Read both from the gauge; never from memory.)
Compound moment (if time): "Pause the worker, then kill Redis": votes returned 200 and vanished. Silent data loss that an HTTP check would call healthy.
Fallbacks: Replay button (last saved state), simulation mode, the 90-second backup video. Keep one terminal visible with `docker compose ps` so it is obviously real.

## Pitch structure (3 minutes, five speakers)
Problem with one number (cost or time of manual chaos tests) -> the idea in one sentence -> live demo (90 s) -> how it works in 3 boxes (planner, injector with guard, analyst) ->
cost and ROI from the ledger -> what is next: CI gate, more targets, Kubernetes. Each speaker 30 s, handoffs rehearsed. No claim we cannot show on screen.

## Hard questions to prepare (two-sentence answers, grounded in what we measured)
1. How is this different from LitmusChaos, Chaos Mesh, Krkn-AI, Harness? (They inject; ours predicts, explains, checks business correctness, applies a fix and proves it, with no cloud account and a local, cheap model.)
2. What if the AI picks something dangerous? (It cannot: it only chooses from an allow-listed catalog; the guard and restore are deterministic code scoped to the project's containers.)
3. What does it cost? (Read the ledger: tokens, rupees, versus the large-model baseline; rules and the cache handle most decisions.)
4. Can it run on Bosch's own infrastructure? (Yes: open-source models and no data leaves the machine; the router can point at an in-house model server.)
5. How accurate is the prediction? (Show predicted vs actual over the runs; it is a hypothesis that improves; wrong predictions are logged and used.)
6. How is the resilience score computed? (Deterministic formula in `score.py`, shown with its breakdown; same inputs, same score.)
7. Does it scale to Kubernetes and real microservices? (The mapper and runner are interfaces; Docker Compose today, Kubernetes manifests next; say it is not built yet.)
8. What did you not test? (State plainly: network latency injection, disk-full, hawkBit if not run.)
9. Why not let the AI fix things automatically? (Bosch's own principle: AI augments, engineers decide; we generate the change and the engineer approves.)
10. How do you stop false alarms? (The guard compares against the measured baseline, not a fixed number.)

## Mapping to what Bosch asked for
| Bosch asks | Where we show it |
|---|---|
| Map services, dependencies, data flows | Live view map, `/api/target`, architecture overview |
| Baseline monitoring | `/api/baseline`, optional Prometheus overlay |
| One safe failure with a kill switch | Live view guard card and Kill button, guard tests |
| Intelligent experiment planning with reasons | Campaign view picks with reason and hypothesis, AI vs random numbers |
| 3+ failure types with errors, slowdowns, recovery | catalog in `docs/TARGETS.md`, Outcome numbers |
| Explain findings, rank by severity, recommend fixes, resilience score | Findings view, `/api/analyze`, `/api/fixes`, `score.py` |
| Demo video, architecture overview, findings report, README, team form | `/api/report.md`, `/api/architecture.md`, README, 3-minute video (confirm the five deliverables with organisers) |
| Low cost, high ROI | Cost view and gauge; works with no key; yearly cost if run nightly |

## Checklists
Before the 13:00 check: target running (or the simulation, clearly labelled); map correct; one crash experiment with the guard tested at least once; replay saved; laptop charged; Docker memory 3-4 GB; the tab kept open.
Before the 18:00 cut: judge-click flow rehearsed three times with a timer; prediction and reveal working; one finding and one fix; backup video recorded; cost figure read from the gauge.
Before submitting: README deploy steps work from a clean clone; all five deliverables open; `python dev.py check` green; submit 30 minutes early.
