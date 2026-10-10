"""CI gate: `python -m app.cli gate --min-score 80 [--budget 5] [--strategy ai|random] [--target voting|hawkbit]`.

Runs a campaign on the target (real Docker when it is running, otherwise the twin), prints findings and the score,
writes the report to reports/, and exits 0 when score >= min-score, 1 when below, 2 on setup errors.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from . import config, report, targets
from .campaign import Engine
from .runner import make_runner


async def gate(args: argparse.Namespace) -> int:
    if args.target:
        config.TARGET = args.target
        config.TARGET_DIR = (config.REPO / "targets" / args.target).resolve()
    try:
        target = targets.load()
        engine = Engine(target, make_runner(target))
        await engine.recover_on_startup()
        ready, detail = await engine.runner.ready()
        if not ready:
            print(f"Target not ready: {detail}")
            return 2
    except Exception as err:
        print(f"Setup failed: {err}")
        return 2
    print(f"Chaos gate on the {target.title} ({engine.runner.kind}), budget {args.budget}, strategy {args.strategy}")
    engine.start_campaign(args.budget, args.strategy)
    assert engine.task is not None
    await engine.task
    a = engine.analysis
    if not a:
        print(f"No analysis: {engine.error}")
        return 2
    for f in a["findings"]:
        print(f"  [{f['severity']}] {f['title']}")
    value = a["score"]["value"]
    paths = report.write_files(engine)
    print(f"Resilience score {value} (minimum {args.min_score}). Report: {paths[0]}")
    return 0 if value >= args.min_score else 1


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gate", help="run a campaign and fail below a resilience score")
    g.add_argument("--min-score", type=float, default=80)
    g.add_argument("--budget", type=int, default=5)
    g.add_argument("--strategy", choices=("ai", "random"), default="ai")
    g.add_argument("--target", choices=sorted(targets.TARGETS), default=None)
    args = p.parse_args()
    sys.exit(asyncio.run(gate(args)))


if __name__ == "__main__":
    main()
