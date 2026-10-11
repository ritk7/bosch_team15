# Chaos Sentinel: architecture overview (one page)

![Architecture](architecture.png)

**Components.** Mapper (compose → dependency graph, manual corrections) · Digital twin (simulation, rehearses every
experiment in seconds) · Injector (crash, hang, network cut, delay, packet loss, CPU throttle, DB overload, traffic spike,
combined; `docker compose -p <project>` only) · Probe (units with unique ids, checked in the app's own data) · Guard (kill
switch) · Score (deterministic formula) · Fixes (approved compose override) · UI (Live, Pipeline, Campaign, Findings, Report,
Cost) · Report and CI gate · Prometheus/Grafana overlay. Targets: **Bosch Eclipse hawkBit** (main) and Docker's voting app (blueprint).

**How the AI makes decisions.** Three agents, each one call to Claude Haiku 4.5 with a strict JSON check and a rules fallback:
1. **Planner** ranks every experiment with a reason using the dependency map, twin results and observed runs, picks the next
   one (riskiest or most informative first, a coverage guardrail) and **predicts** errors, units lost and recovery before injecting.
2. **Risk** writes ranked findings by severity; every finding must cite a real run and numbers that were measured.
3. **Fix** recommends concrete changes; only restart policies can be applied, and only after an engineer approves.
Any answer that invents a number is rejected and the rules output is used; every decision and its cost is in the ledger.

**Safety controls (plain code, never a model).**
- Guard checks every 0.5 s and stops the fault on: error rate ≥ 60% over 3 s, p95 ≥ max(3 s, 10× baseline), collateral
  services down (blast radius: 2 voting, 1 hawkBit), or > 45 s.
- Faults are injected only into a steady app; riskier experiments get shorter windows (6–10 s).
- Restore runs in `finally`; the active fault is saved and undone if the tool itself crashes; a final check repairs
  secondary failures; app-level readiness catches a "running but dead" service.
- Scope: one compose project, services allow-listed; full disk deliberately not injected (shared Docker Desktop disk).

**Workflow.** Map → Rehearse → Plan → Predict → Inject (guarded) → Measure → Restore → Explain → Fix → Approve → Re-run.

Full detail: [ARCHITECTURE.md](ARCHITECTURE.md) · verification: [../JUDGING.md](../JUDGING.md)
