#!/usr/bin/env python3
"""One launcher for every OS.

    python dev.py          backend on :8000 and the UI on :5173, both reloading on save
    python dev.py serve    build the UI and serve everything on :8000 (use this for the demo)
    python dev.py test     backend tests
    python dev.py check    backend tests, then UI type check and build
    python dev.py selftest [--target hawkbit]  real Docker: start the target, crash once, PASS/FAIL per step
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
NPM = shutil.which("npm") or shutil.which("npm.cmd")


def need_backend_deps() -> None:
    try:
        import fastapi, httpx, sklearn, uvicorn, yaml  # noqa: F401,E401
    except ImportError:
        sys.exit("Backend packages are missing. Run:  pip install -r backend/requirements.txt")


def need_npm() -> None:
    if not NPM:
        sys.exit("npm wasn't found. Install Node.js 20 or newer from nodejs.org, then run this again.")
    if not (FRONTEND / "node_modules").exists():
        subprocess.check_call([NPM, "install"], cwd=FRONTEND)


def start_backend(reload: bool) -> subprocess.Popen:
    args = [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8000"]
    return subprocess.Popen(args + (["--reload"] if reload else []), cwd=BACKEND)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dev"
    if cmd == "selftest":
        sys.exit(subprocess.call([sys.executable, "-m", "app.selftest", *sys.argv[2:]], cwd=BACKEND))
    if cmd == "test":
        sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q"], cwd=BACKEND))
    need_backend_deps()
    if cmd == "check":
        need_npm()
        sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q"], cwd=BACKEND)
                 or subprocess.call([NPM, "run", "build"], cwd=FRONTEND))
    if cmd == "serve":
        need_npm()
        subprocess.check_call([NPM, "run", "build"], cwd=FRONTEND)
        procs = [start_backend(reload=False)]
        print("\n  Open http://localhost:8000  (Ctrl+C to stop)\n")
    elif cmd == "dev":
        need_npm()
        procs = [start_backend(reload=True), subprocess.Popen([NPM, "run", "dev"], cwd=FRONTEND)]
        print("\n  Open http://localhost:5173  (API on :8000, Ctrl+C to stop)\n")
    else:
        sys.exit(__doc__)
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()


if __name__ == "__main__":
    main()
