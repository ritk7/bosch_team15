# Running Chaos Sentinel against the real Bosch hawkBit

hawkBit needs about 4 GB for Docker (three containers: hawkBit monolith, MySQL 8, RabbitMQ). Its image is amd64 only, so on
Apple Silicon it runs under emulation; give Docker Desktop **8 GB** (Settings → Resources) on a 16 GB+ machine.
The voting app and hawkBit both use host port 8080, so run one at a time.

## Once
```bash
git clone https://github.com/ritk7/bosch_team15 chaos-sentinel && cd chaos-sentinel
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
docker compose -p hawkbit -f targets/hawkbit/docker-compose.yml pull
```
Optional: copy the team's `backend/.env` over privately (AirDrop or USB, never chat or git) to use the AI models.

## Start and verify
```bash
docker compose -p hawkbit -f targets/hawkbit/docker-compose.yml up -d
# wait until http://localhost:8080 shows the hawkBit login (admin / admin); the first start can take 1-3 minutes
python dev.py selftest --target hawkbit
```
The selftest enables device TargetToken authentication, creates a software module and a distribution set, then runs
simulated devices through the real management API and device API (create target, assign, poll, deploymentBase,
feedback "closed/success"), kills MySQL with the guard armed, and prints PASS/FAIL for each step.

If a step fails, copy the whole output: the adapter's API calls live in `backend/app/targets/hawkbit.py`
(`HawkbitClient`) and are the only part that may need adjusting to the server version.

## Demo
```bash
TARGET=hawkbit RUNNER=auto python dev.py serve      # one port: http://localhost:8000
```
The header badge says **Docker** when the real hawkBit answers. Click `mysql` → Crash, or run a campaign.

## Stop
```bash
docker compose -p hawkbit -f targets/hawkbit/docker-compose.yml down
```
