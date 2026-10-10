# Prompts for Claude Code (paste one at a time, from the repo root)

Spend fewer tokens: one change per message, name the file, paste only the error line, `/compact` after each milestone,
let it run `python dev.py check` itself.

## 0. Plan
Read CLAUDE.md and docs/CONTRACT.md. Plan Milestone 1 in five bullets and wait for my OK.

## 1. Milestone 1 (Day-1 minimum)
Build Milestone 1 with RUNNER=sim first: runner.py (SimRunner and DockerRunner), mapper.py, probe.py, guard.py, one crash
experiment with the kill switch, /api/state, and the Live view with the dependency map and a Kill button. Keep
`python dev.py check` green.

## 2. Docker path
Write `python dev.py selftest`: start targets/voting with Docker Compose, run one real crash experiment on redis, print PASS/FAIL
for each step (app up, map built, baseline, fault injected, guard restored, recovery measured). Unit-test the exact docker
command lines with a fake executor.

## 3. Prediction and the reveal
Add /api/predict (planner agent with a rules fallback), the 100-cell fleet grid, and the predicted-vs-actual reveal in the Live view.

## 4. More failures
Add hang (pause), network_cut, cpu_throttle, traffic_spike and the compound experiment "pause worker then kill redis".
Each must restore itself even if the backend crashes.

## 5. Agents and score
Add the risk and fix agents, score.py, /api/analyze, /api/fixes, /api/apply-fixes (docker-compose.fixes.yml), and the
Findings view with the before/after re-run.

## 6. Campaign and report
Add the campaign (AI planner vs random, runs-to-top-3 computed), report.py (findings report and architecture overview), and the Report view.

## 7. Demo safety
Replay from /api/snapshot and localStorage, README deploy steps, and a 90-second scripted demo path.

## 8. Judge simulation (in chat, not Claude Code)
Act as a Bosch jury: an infra engineer, an AI architect worried about cost and data leaving the plant, and an HR lead. Ask one
question at a time about our solution; after each answer say in one line what was weak.
