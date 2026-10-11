# Verify every claim in 5 minutes

Every number in this repo is measured, never typed in. CI runs the full test suite on every push: [![tests](https://github.com/ritk7/bosch_team15/actions/workflows/tests.yml/badge.svg)](https://github.com/ritk7/bosch_team15/actions/workflows/tests.yml) This page lists each claim, where it lives, and how to check it.

## 1. Run the checks

```bash
./setup.sh voting                                   # or: python3 -m venv .venv && pip install -r backend/requirements.txt
python dev.py check                                 # 135 backend tests, frontend tests, type check, UI build
RUNNER=docker python dev.py serve                   # UI + API on http://localhost:8000 (needs the voting app running)
python scripts/system_test.py http://localhost:8000 # 33 end-to-end checks with number consistency → "PASS: 0 failed"
python dev.py selftest --target hawkbit             # real hawkBit (16 GB+ machine) → 6 PASS lines
```
No Docker? `RUNNER=sim python dev.py` runs the same engine on the digital twin, clearly labelled "Simulation".
No AI key? Every agent falls back to deterministic rules; the product still works end to end.

## 2. Claims and their proof

| Claim | Proof | What you should see |
|---|---|---|
| Works end to end on real Docker | `scripts/system_test.py` | 33 PASS: map, baseline, 4 real experiments, findings, fix, re-run, twin, report, metrics, kill switch, reset |
| Real Bosch hawkBit, not only a mock | [`docs/evidence/hawkbit-2026-10-11/`](docs/evidence/hawkbit-2026-10-11) | `selftest.txt` 6/6 PASS; raw JSON per run; `devices.txt` shows `updateStatus: in_sync` from hawkBit's own API |
| hawkBit findings (15 runs, 0 lost, crash hawkbit → 94.3% errors, MySQL delay → p95 0.56 → 7.3 s) | [`docs/sample-findings-report-hawkbit.md`](docs/sample-findings-report-hawkbit.md) | Ranked findings with per-run evidence and the "All runs" table |
| Silent data loss is caught (HTTP 200 is not trusted) | `backend/app/probe.py`; system test "ghost votes appear only in the combined failure" | Pause worker + crash redis: 225 acknowledged, 23 never stored |
| Safety is code, never a model | [`backend/app/guard.py`](backend/app/guard.py) (about 50 lines) | 4 limits: error rate ≥ 60% over 3 s, p95 ≥ max(3 s, 10× baseline), collateral services down, 45 s |
| Restore always happens | `backend/app/campaign.py` (`finally`), `backend/app/state.py` (`active_fault.json` undone on startup) | Tests: guard trips, every experiment restores, startup restore |
| Scoped to one project | `backend/app/docker_runner.py`: every command is `docker compose -p <project> -f <file>`; services allow-listed | `backend/tests/test_docker_runner.py` pins the exact command lines |
| 3 AI agents, and they can't invent numbers | `planner.py` (`numbers_ok`), `analysis.py`, `agents.py` accept checks | A model answer quoting an unmeasured number is rejected and the rules output is used |
| Intelligent planning is measured, not claimed | Campaign tab; `planner.compare` on the twin | AI vs random runs-to-top-3 computed; on 3-service hawkBit they are about equal (stated honestly) |
| Score is deterministic and can't be gamed | [`backend/app/score.py`](backend/app/score.py) | Worst experiment in full, others at 35%; harmless runs can't raise it; same results, same score (system test) |
| Fix proven by re-run | system test "after-fix score is higher" | Voting blueprint on real Docker: 29.0 → 56.5; crash redis then recovers by itself |
| Low cost | Cost tab; `backend/app/ledger.py` | Every decision logged with tokens and cost; rules decisions cost 0 |
| Observability tool | `docker-compose.observability.yml`, `/metrics` | Prometheus + Grafana; `chaos_service_up`, `chaos_resilience_score` |
| CI gate | `cd backend && python -m app.cli gate --min-score 80` | Exits non-zero below the score |

## 3. Bosch requirements

| Milestone | Requirement | Where |
|---|---|---|
| 1 | App running in the sandbox | hawkBit and the voting blueprint on Docker |
| 1 | Services, dependencies, data flows mapped; manual corrections | `backend/app/mapper.py`; Live tab map; "Correct the map by hand" |
| 1 | Baseline under steady load | `/api/baseline`; injection only into a steady app |
| 1 | First failure injected and observed | Live tab; selftest |
| 1 | Automatic stop on threshold, tested | `guard.py`; tests; real trips in the evidence |
| 2 | AI ranks and selects experiments with reasons | `planner.py`; Campaign tab "Rank every experiment" |
| 2 | 3+ failure types; errors, slowdowns, recovery | 9 types: crash, hang, network cut, delay, packet loss, CPU throttle, DB overload, traffic spike, combined |
| 2 | AI explains, ranks by severity, fixes, score | Findings tab; `analysis.py`, `score.py`, `fixes.py` |
| 2 | Architecture overview, findings report | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/sample-findings-report-hawkbit.md`](docs/sample-findings-report-hawkbit.md) |
| Stretch | Re-run after a fix | Findings → Approve → Re-run (29 → 56.5) |
| Stretch | Live dashboard | Live and Pipeline tabs |
| Stretch | Combined failures | 4 combined experiments, e.g. "device surge while mysql is frozen" |

## 4. Honest limits
- Full disk is deliberately not injected: Docker Desktop's disk is shared by every container on the machine.
- No Kubernetes; Docker Compose only.
- hawkBit needs about 16 GB, so on smaller laptops it runs as the twin (labelled "Simulation"); real-server numbers come from a 24 GB machine.
- The hawkBit "API stays dead after a MySQL disruption" behaviour was seen in 2 of 4 runs on 10 Oct and 0 of 5 on 11 Oct: intermittent.
- In the final hawkBit session the AI risk answer failed our number check, so those findings came from the rules fallback. hawkBit's fixes are code advice, so the before/after re-run is shown on the voting blueprint.
