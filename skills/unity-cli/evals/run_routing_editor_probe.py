#!/usr/bin/env python3
"""Run one local-only editor routing probe with frozen repository checks."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "unity_cli_routing_current",
    HERE / "run_routing_current.py",
)
RUNNER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(RUNNER)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    output = args.output.resolve()
    candidate = RUNNER.DEFAULT_CANDIDATE.resolve()
    package = RUNNER.DEFAULT_PACKAGE_REPO.resolve()
    RUNNER.validate_output_path(output, candidate, package)
    candidate_before = RUNNER.git_snapshot(candidate)
    package_before = RUNNER.git_snapshot(package)
    if not candidate_before["clean"] or not package_before["clean"]:
        raise RuntimeError(
            f"frozen repositories are dirty: "
            f"candidate={candidate_before}, package={package_before}"
        )
    RUNNER._require_revision(candidate_before, "5e3fb6b", "candidate")
    RUNNER._require_revision(package_before, "c4a6205", "package")

    result = RUNNER._run_one(
        RUNNER.load_evals()[0],
        candidate,
        output,
        RUNNER.DEFAULT_CODEX_COMMAND,
        RUNNER.DEFAULT_MODEL,
        RUNNER.DEFAULT_REASONING,
        args.timeout,
        candidate_before,
    )
    candidate_after = RUNNER.git_snapshot(candidate)
    package_after = RUNNER.git_snapshot(package)
    stable = (
        candidate_after == candidate_before
        and package_after == package_before
    )
    print(
        json.dumps(
            {
                "result": result,
                "repositories_stable": stable,
                "candidate_after": candidate_after,
                "package_after": package_after,
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return 0 if result["successful"] and stable else 1


if __name__ == "__main__":
    raise SystemExit(main())
