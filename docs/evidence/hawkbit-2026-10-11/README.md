# Real hawkBit validation, 11 Oct 2026 (Tejas's 24 GB M4 Pro)

Raw evidence as collected; see `VALIDATION.txt` for Tejas's own summary and limitations.

| Run | Devices updated | Stuck | p95 whole run / peak (ms) | Recovery | Guard |
|---|---|---|---|---|---|
| Selftest: crash mysql | 74/74 | 0 | 465 / 3221 | 1 s | tripped (p95 3.2 s ≥ 3.0 s) |
| Crash mysql | 72/72 | 0 | 241 / 1742 | 1 s | not needed |
| Slow network on mysql (+500 ms) | 26/26 | 0 | 7250 / 7974 | 8 s | tripped (p95 5.8 s ≥ 5.6 s) |
| Cut rabbitmq, then crash mysql | 81/81 | 0 | 4985 / 1696 | 7 s | not needed |
| Hang mysql | 85/85 | 0 | 4471 / 6116 | 5 s | tripped (p95 6.1 s ≥ 5.6 s) |

Baseline in the UI session: p95 563 ms, so the adaptive latency limit was 5.6 s (10× baseline).

Notes:
- The "API stays dead after a MySQL disruption" behaviour seen on 10 Oct (2 of 4 runs) did **not** reproduce in these 5 runs.
- The AI risk answer was rejected by the validator, so findings in this session are rules-generated (the guardrail working).
- hawkBit's only recommended fix ("fail fast when mysql is unreachable") is code advice, not auto-applicable, so no before/after
  re-run on hawkBit; the before/after proof is on the voting-app blueprint.
- `hawkbit-findings-report.md` and `hawkbit-architecture.md` were exported before a footer fix: their guard-limit and
  "latency not tested" lines are stale. The app now prints the live limits. Raw evidence is kept unchanged.
