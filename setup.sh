#!/usr/bin/env bash
# One-command setup from a fresh clone (macOS / Linux). Usage: ./setup.sh [voting|hawkbit]
set -euo pipefail
cd "$(dirname "$0")"
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing $1. $2"; exit 1; }; }
need python3 "Install Python 3.12+ (brew install python@3.12)."
need npm "Install Node 20+ (brew install node)."
need git "Install git (brew install git)."
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r backend/requirements.txt
(cd frontend && npm install --silent)
[ -f backend/.env ] || cp backend/.env.example backend/.env
DOCKER=$(command -v docker || echo "$HOME/.docker/bin/docker")
if "$DOCKER" info >/dev/null 2>&1; then
  case "${1:-}" in
    voting)
      [ -d targets/voting ] || git clone -q https://github.com/dockersamples/example-voting-app targets/voting
      (cd targets/voting && "$DOCKER" compose up -d --build)
      "$DOCKER" pull -q alpine:3.20 >/dev/null; "$DOCKER" pull -q nicolaka/netshoot:v0.13 >/dev/null ;;
    hawkbit)
      "$DOCKER" compose -p hawkbit -f targets/hawkbit/docker-compose.yml up -d
      "$DOCKER" pull -q alpine:3.20 >/dev/null; "$DOCKER" pull -q nicolaka/netshoot:v0.13 >/dev/null ;;
  esac
else
  echo "Docker is not running: the simulation still works (RUNNER=sim)."
fi
.venv/bin/python dev.py check
echo
echo "Ready. Start:  RUNNER=auto .venv/bin/python dev.py   (add TARGET=hawkbit for hawkBit), then open http://localhost:5173"
