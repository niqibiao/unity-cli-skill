"""Refresh the local builtin-snapshot test fixture from a live editor service.

The offline builtin snapshot is no longer a checked-in artifact.  Local tests
that need real contracts read ``local_fixtures/builtin_registry_snapshot.v1.json``,
and this script regenerates that file from the live registry: fetch, validate,
verify serializer parity, project to builtin-only, and write atomically.

The serializer parity step is the live-seam drift gate: it proves the Python
canonical writer reproduces the package's partition fingerprints and registry
generation byte for byte, so contract data (command ids, arguments, schemas,
descriptions) cannot silently diverge between the two implementations.

    python refresh_local_fixture.py [--port 14500]

This file is intentionally untracked and must not be committed.
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path


CLI_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = CLI_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cli.registry_protocol import (  # noqa: E402
    compute_partition_fingerprint,
    compute_registry_generation,
    validate_snapshot,
)

TARGET = CLI_DIR / "local_fixtures" / "builtin_registry_snapshot.v1.json"


def _fetch_live_snapshot(port):
    payload = json.dumps({
        "invocation": {
            "sessionId": "",
            "command": {
                "commandNamespace": "command",
                "action": "registry.snapshot",
            },
            "argsJson": "{}",
        },
    }).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/CSharpConsole/command",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        envelope = json.loads(response.read().decode("utf-8"))
    if envelope.get("type") != "ok":
        raise RuntimeError(
            f"registry.snapshot failed: {envelope.get('summary')}"
        )
    data = envelope.get("dataJson")
    data = json.loads(data) if isinstance(data, str) else data
    result = data.get("resultJson")
    result = json.loads(result) if isinstance(result, str) else result
    return validate_snapshot(result, required_included=("builtin", "custom"))


def _check_serializer_parity(snapshot):
    failures = []
    for partition in ("builtin", "custom"):
        computed = compute_partition_fingerprint(
            partition,
            snapshot[partition]["commands"],
        )
        if computed != snapshot[partition]["fingerprint"]:
            failures.append(
                f"{partition} fingerprint drift: live "
                f"{snapshot[partition]['fingerprint']} != recomputed {computed}"
            )
    generation = compute_registry_generation(
        snapshot["builtin"]["count"],
        snapshot["builtin"]["fingerprint"],
        snapshot["custom"]["count"],
        snapshot["custom"]["fingerprint"],
    )
    if generation != snapshot["registryGeneration"]:
        failures.append(
            f"generation drift: live {snapshot['registryGeneration']} "
            f"!= recomputed {generation}"
        )
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=14500)
    options = parser.parse_args()

    snapshot = _fetch_live_snapshot(options.port)
    parity = _check_serializer_parity(snapshot)
    if parity:
        for failure in parity:
            print(f"PARITY   {failure}")
        return 1
    print("parity   canonical writer matches the live serializer")

    projected = json.loads(json.dumps(snapshot))
    projected["custom"]["included"] = False
    projected["custom"]["commands"] = []

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(
        json.dumps(projected, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(
        f"wrote {TARGET.name}: {projected['builtin']['count']} builtin "
        f"command(s), generation {projected['registryGeneration'][:12]}..."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
