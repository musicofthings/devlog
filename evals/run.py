"""CLI entry: python -m evals.run [--live] [--json]

Offline by default (template summarizer). Pass --live to exercise Claude when
ANTHROPIC_API_KEY is set (still scored with groundedness/style rubrics only).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from evals.cases import CASES, run_all


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Daily Dev Log Phase 1 evals")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Also score a live Claude post for the e2e case (requires ANTHROPIC_API_KEY)",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args(argv)

    live = args.live
    if live and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: --live requires ANTHROPIC_API_KEY", file=sys.stderr)
        return 2

    results = run_all(live=live)

    if args.json:
        payload = [
            {
                "case_id": r.case_id,
                "passed": r.passed,
                "checks": [
                    {"name": c.name, "passed": c.passed, "detail": c.detail} for c in r.checks
                ],
            }
            for r in results
        ]
        print(json.dumps(payload, indent=2))
    else:
        print("Daily Dev Log - Phase 1 evals")
        print(f"mode: {'live+offline' if live else 'offline (template)'}")
        print()
        desc = {c.case_id: c.description for c in CASES}
        for r in results:
            mark = "PASS" if r.passed else "FAIL"
            print(f"[{mark}] {r.case_id}")
            print(f"       {desc.get(r.case_id, '')}")
            for c in r.checks:
                cmark = "OK" if c.passed else "FAIL"
                detail = f" - {c.detail}" if c.detail else ""
                print(f"       [{cmark}] {c.name}{detail}")
            print()

        passed = sum(1 for r in results if r.passed)
        print(f"Summary: {passed}/{len(results)} cases passed")

    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
