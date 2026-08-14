#!/usr/bin/env python3
"""Run a fresh, candidate-only, mutation-free unity-cli routing acceptance.

The runner intentionally creates a new output root, evaluates eight prompts in
eight independent ephemeral Codex sessions, rejects traces outside a narrow
read-only command allowlist, records content-addressed provenance, and invokes the
deterministic 89-case micro grader. It never connects to Unity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath


HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
CLI_DIR = SKILL_DIR / "scripts" / "cli"
EVALS_PATH = HERE / "evals.json"
ROUTE_ORACLE_PATH = HERE / "route_cases.json"
TRIGGER_ORACLE_PATH = HERE / "trigger_queries.json"
ROUTE_SCHEMA_PATH = HERE / "routing-output.schema.json"
TRIGGER_SCHEMA_PATH = HERE / "routing-trigger-output.schema.json"
GRADER_PATH = HERE / "benchmark.py"
REGISTRY_SNAPSHOT_PATH = CLI_DIR / "local_fixtures" / "builtin_registry_snapshot.v1.json"
ROUTING_OVERLAY_PATH = CLI_DIR / "routing_overlay.json"

REPO_ROOT = SKILL_DIR.parent.parent

DEFAULT_CANDIDATE = Path(os.environ.get("ROUTING_EVAL_CANDIDATE", REPO_ROOT))
DEFAULT_PACKAGE_REPO = Path(
    os.environ.get("ROUTING_EVAL_PACKAGE_REPO", REPO_ROOT.parent)
)
DEFAULT_CODEX_COMMAND = Path(os.environ.get("CODEX_EVAL_COMMAND", "codex"))
DEFAULT_MODEL = os.environ.get("CODEX_EVAL_MODEL", "gpt-5.4")
DEFAULT_REASONING = os.environ.get("CODEX_EVAL_REASONING", "medium")

CASE_RE = re.compile(r"(?m)^(r\d{2}|c\d{2}|t\d{2}):")
EVAL_SLUGS = {
    1: "editor",
    2: "objects",
    3: "components-materials",
    4: "prefabs",
    5: "scenes-assets",
    6: "capture-profiler",
    7: "control",
    8: "activation",
}
COMPLETED_ITEM_TYPES = {
    "agent_message",
    "reasoning",
    "command_execution",
    "todo_list",
}
POWERSHELL_TRANSPORT_RE = re.compile(
    r'^(?:"(?P<quoted_exe>[^"\r\n]+)"|(?P<bare_exe>\S+))'
    r'\s+-Command\s+"(?P<payload>[^"\r\n]*)"$',
    re.IGNORECASE,
)
TRANSIENT_RECONNECT_RE = re.compile(
    r"^Reconnecting\.\.\. \d+/\d+ "
    r"\(stream disconnected before completion: .+\)$",
    re.IGNORECASE,
)

HARNESS = """\
This is one independent, read-only routing evaluation of the unity-cli skill in
the current checkout. Read skills/unity-cli/SKILL.md completely and follow only
that checkout's documented routing/reference guidance. The case prompt's phrase
"do not call tools" means do not perform the requested Unity work; repository
reads and the single offline discovery surface below remain permitted.
The workspace-write sandbox is used only so the bundled Python CLI can start;
any checkout write invalidates the run.

Do not connect to or start Unity. Do not use setup, status, health, command,
batch, refresh, exec, catalog mutation, snippet execution, or network access.
For route cases in evaluations 1-7, use offline discovery rather than guessing
command IDs.
The only CLI operation permitted is the bundled command in this exact form:
`python -B '<candidate>/skills/unity-cli/scripts/cli/cs.py' list-commands
--offline ... --json`. The editor evaluation must inspect both its core and
advanced tiers. Other shell activity is limited to simple reads of SKILL.md,
its Markdown references, AGENTS.md, or CLAUDE.md.
The only permitted document reader is `Get-Content`, optionally with `-Raw`,
`-Path`, or `-LiteralPath`, and every target must be one exact file. Do not use
Get-ChildItem, Select-String, rg, wildcards, pipelines, or enumeration. Start
with `Get-Content -Raw 'skills/unity-cli/SKILL.md'`, then open only an exact
Markdown reference path explicitly named by that file.
Run each permitted read directly, without pipelines, command chaining,
redirection, command substitution, loops, or wrapper scripts.

Do not inspect Git history, tests, implementation files, generated registry
data, routing overlays, evals, reports, .scratch, other worktrees, or another
skill version. Do not create scratch files. Return only the JSON object requested
by the case prompt, in the same case order. Use canonical slash command IDs.
For route results, the schema represents `args` as an array of
`{{"name": "...", "valueJson": "<valid JSON>"}}` entries so arbitrary typed
argument values remain schema-safe; use an empty array when no args are explicit.
For blocked results, always use an empty args array and a concise non-empty
deny-policy reason; denied discovery intentionally exposes no executable schema.

Case prompt:
{user_task}
"""


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _read_json(path):
    return json.loads(Path(path).read_text("utf-8"))


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _ignored_state(repository):
    repository = Path(repository).resolve()
    raw = subprocess.check_output(
        [
            "git",
            "-C",
            str(repository),
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "-z",
        ]
    )
    entries = []
    for raw_relative in raw.split(b"\0"):
        if not raw_relative:
            continue
        relative = os.fsdecode(raw_relative)
        path = repository / relative
        if path.is_symlink():
            kind = "symlink"
            digest = hashlib.sha256(
                os.readlink(path).encode("utf-8", errors="surrogatepass")
            ).hexdigest()
            size = 0
        elif path.is_file():
            kind = "file"
            digest = _sha256(path)
            size = path.stat().st_size
        else:
            kind = "other"
            digest = ""
            size = 0
        entries.append(
            {
                "path": relative.replace("\\", "/"),
                "kind": kind,
                "size": size,
                "sha256": digest,
            }
        )
    entries.sort(key=lambda item: item["path"])
    encoded = json.dumps(
        entries,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "entry_count": len(entries),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _hash_record(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": _sha256(path)}


def _is_within(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def validate_output_path(output, candidate, package_repo):
    output = Path(output).resolve()
    candidate = Path(candidate).resolve()
    package_repo = Path(package_repo).resolve()
    if output.exists():
        raise ValueError(f"output root already exists: {output}")
    if _is_within(output, candidate) or _is_within(output, package_repo):
        raise ValueError(
            "output root must be outside both frozen repositories: "
            f"{output}"
        )


def load_evals():
    return _read_json(EVALS_PATH)["evals"]


def _case_ids(eval_item):
    return CASE_RE.findall(eval_item["prompt"])


def case_count(evals):
    return sum(len(_case_ids(item)) for item in evals)


def schema_for_eval(eval_item):
    case_ids = _case_ids(eval_item)
    if case_ids and all(case_id.startswith("t") for case_id in case_ids):
        return TRIGGER_SCHEMA_PATH
    return ROUTE_SCHEMA_PATH


def build_prompt(eval_item):
    return HARNESS.format(user_task=eval_item["prompt"])


def build_codex_command(
    codex_command,
    candidate,
    schema,
    output_last_message,
    model,
    reasoning,
):
    return [
        str(codex_command),
        "--ask-for-approval",
        "on-request",
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--model",
        model,
        "--config",
        f'model_reasoning_effort="{reasoning}"',
        "--config",
        'approvals_reviewer="auto_review"',
        "--config",
        'windows.sandbox="unelevated"',
        "--sandbox",
        "workspace-write",
        "--output-schema",
        str(schema),
        "--output-last-message",
        str(output_last_message),
        "-C",
        str(candidate),
        "-",
    ]


def collect_static_inputs():
    paths = {
        "evals": EVALS_PATH,
        "route_oracle": ROUTE_ORACLE_PATH,
        "trigger_oracle": TRIGGER_ORACLE_PATH,
        "registry_snapshot": REGISTRY_SNAPSHOT_PATH,
        "routing_overlay": ROUTING_OVERLAY_PATH,
        "route_schema": ROUTE_SCHEMA_PATH,
        "trigger_schema": TRIGGER_SCHEMA_PATH,
        "grader": GRADER_PATH,
        "runner": Path(__file__),
    }
    return {name: _hash_record(path) for name, path in paths.items()}


def _git_output(repository, *args):
    return subprocess.check_output(
        ["git", "-C", str(repository), *args],
        text=True,
        encoding="utf-8",
        errors="replace",
    ).strip()


def git_snapshot(repository):
    repository = Path(repository).resolve()
    commit = _git_output(repository, "rev-parse", "HEAD")
    status_text = _git_output(
        repository,
        "status",
        "--porcelain",
        "--untracked-files=all",
    )
    status = status_text.splitlines() if status_text else []
    return {
        "path": str(repository),
        "commit": commit,
        "status": status,
        "clean": not status,
        "ignored_state": _ignored_state(repository),
    }


def _require_revision(snapshot, expected, label):
    if expected and not snapshot["commit"].casefold().startswith(
        expected.casefold()
    ):
        raise ValueError(
            f"{label} revision mismatch: expected {expected}, "
            f"got {snapshot['commit']}"
        )


def candidate_skill_inputs(candidate):
    candidate = Path(candidate).resolve()
    skill_root = candidate / "skills" / "unity-cli"
    paths = [skill_root / "SKILL.md"]
    paths.extend(
        path
        for path in (candidate / "AGENTS.md", candidate / "CLAUDE.md")
        if path.is_file()
    )
    references = skill_root / "references"
    if references.is_dir():
        paths.extend(sorted(references.glob("*.md")))
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise ValueError(f"candidate skill inputs are missing: {missing}")
    return {
        str(path.relative_to(candidate)).replace("\\", "/"): _hash_record(path)
        for path in paths
    }


def candidate_contract_inputs(candidate):
    cli_dir = (
        Path(candidate).resolve()
        / "skills"
        / "unity-cli"
        / "scripts"
        / "cli"
    )
    paths = {
        "registry_snapshot": (
            cli_dir / "local_fixtures" / "builtin_registry_snapshot.v1.json"
        ),
        "routing_overlay": cli_dir / "routing_overlay.json",
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise ValueError(f"candidate contract inputs are missing: {missing}")
    return {name: _hash_record(path) for name, path in paths.items()}


def _tokens(command):
    return [
        token[1:-1] if len(token) >= 2 and token[0] == token[-1] and token[0] in "'\"" else token
        for token in re.findall(r'"[^"]*"|\'[^\']*\'|\S+', command)
    ]


def _normalize_command(command):
    if isinstance(command, str):
        return " ".join(command.split())
    return json.dumps(command, ensure_ascii=False, sort_keys=True)


def _command_payload(command):
    """Return the command inside Codex's exact PowerShell transport wrapper."""
    normalized = _normalize_command(command)
    while "\\\\" in normalized:
        normalized = normalized.replace("\\\\", "\\")
    normalized = normalized.replace('\\"', '"')
    tokens = _tokens(normalized)
    if not tokens:
        return "", "empty command execution"
    first = PureWindowsPath(tokens[0]).name.casefold()
    powershell_names = {
        "pwsh",
        "pwsh.exe",
        "powershell",
        "powershell.exe",
    }
    if first not in powershell_names:
        return normalized, None
    match = POWERSHELL_TRANSPORT_RE.fullmatch(normalized)
    if not match:
        return "", (
            "PowerShell trace transport must be exactly "
            "<pwsh|powershell> -Command \"<single command>\""
        )
    executable = match.group("quoted_exe") or match.group("bare_exe")
    executable_name = PureWindowsPath(executable).name.casefold()
    if executable_name not in powershell_names:
        return "", "PowerShell trace transport executable is not recognized"
    slash_executable = executable.replace("\\", "/").casefold()
    is_bare = "/" not in slash_executable
    is_pwsh_install = bool(
        re.fullmatch(
            r"[a-z]:/program files/powershell/[^/]+/pwsh\.exe",
            slash_executable,
        )
    )
    is_windows_powershell = bool(
        re.fullmatch(
            r"[a-z]:/windows/system32/windowspowershell/v1\.0/"
            r"powershell\.exe",
            slash_executable,
        )
    )
    if not (is_bare or is_pwsh_install or is_windows_powershell):
        return "", "PowerShell trace transport path is not a recognized install"
    payload = match.group("payload").strip()
    if not payload:
        return "", "PowerShell trace transport has an empty payload"
    return payload, None


def _resolved_candidate_path(value, candidate):
    path = Path(value)
    if not path.is_absolute():
        path = Path(candidate) / path
    return path.resolve()


def _same_path(left, right):
    return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(
        str(Path(right).resolve())
    )


def _offline_discovery_violation(tokens, candidate):
    expected_cli = (
        Path(candidate).resolve()
        / "skills"
        / "unity-cli"
        / "scripts"
        / "cli"
        / "cs.py"
    )
    cs_indexes = [
        index
        for index, token in enumerate(tokens)
        if token.casefold().endswith(".py")
        and _same_path(
            _resolved_candidate_path(token, candidate),
            expected_cli,
        )
    ]
    if len(cs_indexes) != 1:
        return "Python trace command must target the bundled unity-cli cs.py once"
    cs_index = cs_indexes[0]
    python_options = tokens[1:cs_index]
    if python_options.count("-B") != 1:
        return "offline discovery must invoke Python with exactly one literal -B"
    if python_options.count("-u") > 1 or any(
        token not in {"-B", "-u"}
        for token in python_options
    ):
        return "Python may use only -B/-u before the bundled cs.py path"
    tail = tokens[cs_index + 1 :]
    if not tail or tail[0].casefold() != "list-commands":
        return "only the list-commands CLI subcommand is allowed"
    if "--offline" not in [token.casefold() for token in tail]:
        return "list-commands must include --offline"

    no_value = {"--offline", "--json", "--verbose"}
    with_value = {"--view", "--domain", "--tier", "--id"}
    index = 1
    while index < len(tail):
        option = tail[index].casefold()
        if option in no_value:
            index += 1
            continue
        if (
            option in with_value
            and index + 1 < len(tail)
            and not tail[index + 1].startswith("-")
        ):
            index += 2
            continue
        return f"unsupported offline discovery argument: {tail[index]}"
    return None


def _offline_discovery_query(command, candidate):
    payload, transport_error = _command_payload(command)
    if transport_error:
        return None
    tokens = _tokens(payload)
    if not tokens:
        return None
    first = PureWindowsPath(tokens[0]).name.casefold()
    if first not in {"python", "python.exe", "py", "py.exe"}:
        return None
    if _offline_discovery_violation(tokens, candidate):
        return None
    cs_index = next(
        index
        for index, token in enumerate(tokens)
        if token.casefold().endswith(".py")
        and _same_path(
            _resolved_candidate_path(token, candidate),
            Path(candidate).resolve()
            / "skills"
            / "unity-cli"
            / "scripts"
            / "cli"
            / "cs.py",
        )
    )
    tail = tokens[cs_index + 1 :]
    domains = [
        tail[index + 1]
        for index, token in enumerate(tail[:-1])
        if token.casefold() == "--domain"
    ]
    tiers = [
        tail[index + 1]
        for index, token in enumerate(tail[:-1])
        if token.casefold() == "--tier"
    ]
    return {
        "domains": [value.casefold() for value in domains],
        "tiers": [value.casefold() for value in tiers],
    }


def _valid_offline_discovery_output(output):
    try:
        envelope = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return False
    if (
        not isinstance(envelope, dict)
        or envelope.get("ok") is not True
        or not isinstance(envelope.get("summary"), str)
        or not isinstance(envelope.get("data"), dict)
    ):
        return False
    data = envelope["data"]
    if (
        type(data.get("schemaVersion")) is not int
        or data["schemaVersion"] != 1
    ):
        return False
    kind = data.get("kind")
    collection_by_kind = {
        "domain-index": "domains",
        "route-cards": "routes",
        "contract-bundle": "selected",
    }
    collection = collection_by_kind.get(kind)
    return collection is not None and isinstance(data.get(collection), list)


def _markdown_read_violation(tokens, candidate):
    if PureWindowsPath(tokens[0]).name.casefold() != "get-content":
        return f"shell command is outside the read allowlist: {tokens[0]}"
    targets = []
    index = 1
    while index < len(tokens):
        token = tokens[index]
        option = token.casefold()
        if option == "-raw":
            index += 1
            continue
        if option in {"-path", "-literalpath"}:
            if index + 1 >= len(tokens) or tokens[index + 1].startswith("-"):
                return f"{token} requires one Markdown path"
            targets.append(tokens[index + 1])
            index += 2
            continue
        if token.startswith("-"):
            return f"unsupported Get-Content argument: {token}"
        targets.append(token)
        index += 1
    if not targets:
        return "Get-Content requires at least one Markdown path"

    candidate = Path(candidate).resolve()
    exact_files = {
        os.path.normcase(str(candidate / "AGENTS.md")),
        os.path.normcase(str(candidate / "CLAUDE.md")),
        os.path.normcase(
            str(candidate / "skills" / "unity-cli" / "SKILL.md")
        ),
    }
    references = (
        candidate / "skills" / "unity-cli" / "references"
    ).resolve()
    for target in targets:
        if any(marker in target for marker in ("*", "?", "[", "]")):
            return f"wildcard Markdown reads are not allowed: {target}"
        resolved = _resolved_candidate_path(target, candidate)
        normalized = os.path.normcase(str(resolved))
        is_exact = normalized in exact_files
        is_reference = (
            _same_path(resolved.parent, references)
            and resolved.suffix.casefold() == ".md"
        )
        if not _is_within(resolved, candidate):
            return f"read path is outside the candidate checkout: {target}"
        if not (is_exact or is_reference):
            return f"read path is outside the allowed Markdown surface: {target}"
    return None


def _command_violation(command, candidate):
    normalized, transport_error = _command_payload(command)
    if transport_error:
        return transport_error
    lowered = normalized.casefold()
    tokens = _tokens(normalized)
    if not tokens:
        return "empty command execution"
    first = PureWindowsPath(tokens[0]).name.casefold()
    if any(
        token in {".", "./", ".\\"}
        or token.replace("\\", "/").startswith("../")
        or "/../" in token.replace("\\", "/")
        for token in tokens[1:]
    ):
        return "broad current-directory reads and parent traversal are not allowed"
    if any(
        marker in normalized
        for marker in ("\n", "\r", "|", ";", "&", ">", "<", "`")
    ):
        return "shell composition, pipelines, and redirection are not allowed"
    if "$(" in normalized or "&&" in normalized or "||" in normalized:
        return "nested or chained shell commands are not allowed"
    if re.search(
        r"(?:https?://|\b(?:curl|wget)(?:\.exe)?\b|"
        r"\binvoke-(?:webrequest|restmethod)\b)",
        lowered,
    ):
        return "network access is not allowed"
    if first in {
        "git",
        "git.exe",
        "unity",
        "unity.exe",
        "unityhub",
        "unityhub.exe",
        "unitystart",
        "unitystart.cmd",
    }:
        return "Git and Unity processes are not allowed"
    if re.search(
        r"\b(?:set-content|add-content|out-file|new-item|remove-item|"
        r"move-item|copy-item|rename-item|tee-object|start-process)\b",
        lowered,
    ):
        return "write-producing shell commands are not allowed"
    slash_lowered = lowered.replace("\\", "/")
    if any(
        marker in slash_lowered
        for marker in (
            "/evals/",
            "route_cases.json",
            "trigger_queries.json",
            "/reports/",
            "/.scratch/",
            "routing_overlay.json",
            "builtin_registry_snapshot",
        )
    ):
        return "evaluation, report, scratch, and generated routing inputs are hidden"

    if first in {"python", "python.exe", "py", "py.exe"}:
        return _offline_discovery_violation(tokens, candidate)

    if "scripts/cli" in slash_lowered:
        return "CLI implementation files may not be inspected"
    return _markdown_read_violation(tokens, candidate)


def _event_output(item):
    output = item.get("aggregated_output", item.get("output", ""))
    if isinstance(output, str):
        return output
    return json.dumps(output, ensure_ascii=False)


def final_matches_trace(events, final_text):
    messages = [
        event["item"].get("text", "")
        for event in events
        if event.get("type") == "item.completed"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "agent_message"
        and isinstance(event["item"].get("text"), str)
    ]
    return bool(messages) and messages[-1].strip() == final_text.strip()


def validate_trace(events, candidate, parse_errors=0):
    violations = []
    if parse_errors:
        violations.append(f"trace contains {parse_errors} non-JSON line(s)")
    completed_turns = [
        event for event in events if event.get("type") == "turn.completed"
    ]
    if len(completed_turns) != 1:
        violations.append(
            "trace must contain exactly one turn.completed event; "
            f"got {len(completed_turns)}"
        )
    transient_reconnections = [
        event
        for event in events
        if event.get("type") == "error"
        and isinstance(event.get("message"), str)
        and TRANSIENT_RECONNECT_RE.fullmatch(event["message"])
    ]
    fatal_errors = [
        event
        for event in events
        if event.get("type") == "error"
        and event not in transient_reconnections
    ]
    if fatal_errors:
        violations.append("trace contains a top-level error event")

    commands = []
    offline_discovery_queries = []
    tool_output_bytes = 0
    nonzero_tool_exits = 0
    for event in events:
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            violations.append("item.completed must contain an item object")
            continue
        item_type = item.get("type")
        if item_type not in COMPLETED_ITEM_TYPES:
            violations.append(f"completed item type is not allowed: {item_type}")
            continue
        if item_type != "command_execution":
            continue
        command = _normalize_command(item.get("command", ""))
        commands.append(command)
        event_output = _event_output(item)
        tool_output_bytes += len(event_output.encode("utf-8"))
        if item.get("exit_code") not in (None, 0):
            nonzero_tool_exits += 1
        command_error = _command_violation(command, Path(candidate))
        if command_error:
            violations.append(f"{command_error}: {command}")
        discovery = _offline_discovery_query(command, candidate)
        if (
            discovery is not None
            and item.get("exit_code") == 0
            and event_output.strip()
            and _valid_offline_discovery_output(event_output)
        ):
            offline_discovery_queries.append(discovery)

    usage = completed_turns[0].get("usage", {}) if len(completed_turns) == 1 else {}
    if not isinstance(usage, dict):
        usage = {}
        violations.append("turn.completed usage must be an object")
    total_tokens = usage.get("total_tokens")
    if not isinstance(total_tokens, int):
        total_tokens = sum(
            value
            for key in ("input_tokens", "output_tokens")
            if isinstance((value := usage.get(key)), int)
        )
    return {
        "safe": not violations,
        "violations": violations,
        "turn_completed": len(completed_turns),
        "transient_reconnections": len(transient_reconnections),
        "tool_calls": len(commands),
        "commands": commands,
        "offline_discovery_calls": len(offline_discovery_queries),
        "offline_discovery_queries": offline_discovery_queries,
        "tool_output_bytes": tool_output_bytes,
        "nonzero_tool_exits": nonzero_tool_exits,
        "usage": usage,
        "total_tokens": total_tokens,
    }


def discovery_evidence_satisfied(eval_id, trace):
    queries = trace["offline_discovery_queries"]
    if eval_id == 8:
        return True
    if eval_id == 1:
        editor_tiers = {
            tier
            for query in queries
            if "editor" in query["domains"]
            for tier in query["tiers"]
        }
        return (
            trace["offline_discovery_calls"] >= 2
            and {"core", "advanced"}.issubset(editor_tiers)
        )
    return trace["offline_discovery_calls"] >= 1


def _parse_events(stdout):
    events = []
    parse_errors = 0
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            parse_errors += 1
            continue
        if isinstance(value, dict):
            events.append(value)
        else:
            parse_errors += 1
    return events, parse_errors


def _text_from_timeout(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _terminate_process_tree(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()


def _run_codex(command, prompt, child_env, timeout_seconds):
    popen_kwargs = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child_env,
        **popen_kwargs,
    )
    try:
        stdout, stderr = process.communicate(
            input=prompt,
            timeout=timeout_seconds,
        )
        return stdout, stderr, process.returncode, False
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        stdout, stderr = process.communicate()
        stderr += f"\nTimed out after {timeout_seconds} seconds.\n"
        return stdout, stderr, 124, True


def _run_one(
    eval_item,
    candidate,
    output_root,
    codex_command,
    model,
    reasoning,
    timeout_seconds,
    expected_candidate_snapshot=None,
):
    eval_name = f"eval-{eval_item['id']:03d}-{EVAL_SLUGS[eval_item['id']]}"
    run_dir = Path(output_root) / eval_name / "new_skill" / "run-1"
    outputs = run_dir / "outputs"
    outputs.mkdir(parents=True, exist_ok=False)
    schema = schema_for_eval(eval_item)
    prompt = build_prompt(eval_item)
    prompt_path = run_dir / "prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    route_path = outputs / "route.json"
    command = build_codex_command(
        codex_command,
        candidate,
        schema,
        route_path,
        model,
        reasoning,
    )

    started_at = _utc_now()
    started = time.perf_counter()
    child_env = os.environ.copy()
    child_env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        stdout, stderr, exit_code, timed_out = _run_codex(
            command,
            prompt,
            child_env,
            timeout_seconds,
        )
    except OSError as error:
        stdout = ""
        stderr = f"{type(error).__name__}: {error}\n"
        exit_code = 127
        timed_out = False
    duration = time.perf_counter() - started
    ended_at = _utc_now()

    trace_path = outputs / "trace.jsonl"
    stderr_path = outputs / "stderr.txt"
    trace_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    events, parse_errors = _parse_events(stdout)
    trace = validate_trace(events, candidate, parse_errors=parse_errors)
    discovery_satisfied = discovery_evidence_satisfied(eval_item["id"], trace)
    final_text = route_path.read_text("utf-8") if route_path.is_file() else ""
    final_matches = (
        route_path.is_file() and final_matches_trace(events, final_text)
    )
    if not final_matches:
        trace["violations"].append(
            "output-last-message does not match the final completed agent message"
        )
        trace["safe"] = False
    checkout_after = git_snapshot(candidate)
    candidate_state_stable = (
        expected_candidate_snapshot is None
        or checkout_after == expected_candidate_snapshot
    )
    metrics = {
        **trace,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_seconds": round(duration, 3),
        "started_at": started_at,
        "ended_at": ended_at,
        "prompt_bytes": len(prompt.encode("utf-8")),
        "trace_stdout_bytes": len(stdout.encode("utf-8")),
        "stderr_bytes": len(stderr.encode("utf-8")),
        "final_output_bytes": route_path.stat().st_size if route_path.exists() else 0,
        "final_matches_trace": final_matches,
        "discovery_evidence_satisfied": discovery_satisfied,
        "checkout_after": checkout_after,
    }
    metrics_path = outputs / "metrics.json"
    _write_json(metrics_path, metrics)

    artifact_hashes = {
        "prompt": _hash_record(prompt_path),
        "schema": _hash_record(schema),
        "trace": _hash_record(trace_path),
        "stderr": _hash_record(stderr_path),
        "metrics": _hash_record(metrics_path),
        "final": _hash_record(route_path) if route_path.is_file() else None,
    }
    metadata = {
        "eval_id": eval_item["id"],
        "eval_name": eval_name,
        "configuration": "current",
        "run_number": 1,
        "case_ids": _case_ids(eval_item),
        "model": model,
        "reasoning_effort": reasoning,
        "approval_policy": "on-request",
        "approvals_reviewer": "auto_review",
        "windows_sandbox": "unelevated",
        "sandbox": "workspace-write",
        "ephemeral": True,
        "argv": command,
        "artifact_sha256": artifact_hashes,
    }
    metadata_path = run_dir / "eval_metadata.json"
    _write_json(metadata_path, metadata)
    return {
        "eval_id": eval_item["id"],
        "eval_name": eval_name,
        "case_count": len(_case_ids(eval_item)),
        "exit_code": exit_code,
        "trace_safe": trace["safe"],
        "trace_violations": trace["violations"],
        "turn_completed": trace["turn_completed"],
        "transient_reconnections": trace["transient_reconnections"],
        "tool_calls": trace["tool_calls"],
        "tool_output_bytes": trace["tool_output_bytes"],
        "offline_discovery_calls": trace["offline_discovery_calls"],
        "offline_discovery_queries": trace["offline_discovery_queries"],
        "discovery_evidence_satisfied": discovery_satisfied,
        "total_tokens": trace["total_tokens"],
        "candidate_clean_after": checkout_after["clean"],
        "candidate_commit_after": checkout_after["commit"],
        "candidate_ignored_state_after": checkout_after["ignored_state"],
        "candidate_state_stable": candidate_state_stable,
        "route_output_exists": route_path.is_file(),
        "final_matches_trace": final_matches,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": round(duration, 3),
        "artifact_sha256": {
            **artifact_hashes,
            "metadata": _hash_record(metadata_path),
        },
        "successful": (
            exit_code == 0
            and trace["safe"]
            and trace["nonzero_tool_exits"] == 0
            and discovery_satisfied
            and checkout_after["clean"]
            and candidate_state_stable
            and route_path.is_file()
        ),
    }


def _codex_version(codex_command):
    return subprocess.check_output(
        [str(codex_command), "--version"],
        text=True,
        encoding="utf-8",
        errors="replace",
    ).strip()


def _run_grader(output_root):
    return subprocess.run(
        [
            sys.executable,
            "-B",
            str(GRADER_PATH),
            "grade-current",
            str(output_root),
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _argv_template(codex_command, candidate, model, reasoning):
    return build_codex_command(
        codex_command,
        candidate,
        Path("<schema-selected-per-eval>"),
        Path("<fresh-output-root>/<eval>/new_skill/run-1/outputs/route.json"),
        model,
        reasoning,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Run fresh candidate-only unity-cli routing acceptance."
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument(
        "--package-repo",
        type=Path,
        default=DEFAULT_PACKAGE_REPO,
    )
    parser.add_argument(
        "--codex-command",
        type=Path,
        default=DEFAULT_CODEX_COMMAND,
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--reasoning", default=DEFAULT_REASONING)
    parser.add_argument("--expect-candidate")
    parser.add_argument("--expect-package")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("--timeout must be positive")

    output_root = args.output.resolve()
    candidate = args.candidate.resolve()
    package_repo = args.package_repo.resolve()
    try:
        validate_output_path(output_root, candidate, package_repo)
        evals = load_evals()
        if len(evals) != 8 or case_count(evals) != 89:
            raise ValueError("routing acceptance must contain 8 evals and 89 cases")
        all_case_ids = [
            case_id for eval_item in evals for case_id in _case_ids(eval_item)
        ]
        if len(set(all_case_ids)) != 89:
            raise ValueError("routing acceptance case IDs must be unique")
        candidate_before = git_snapshot(candidate)
        package_before = git_snapshot(package_repo)
        if not candidate_before["clean"] or not package_before["clean"]:
            raise ValueError(
                "candidate and package repositories must both be clean: "
                f"candidate={candidate_before['status']}, "
                f"package={package_before['status']}"
            )
        _require_revision(
            candidate_before, args.expect_candidate, "candidate"
        )
        _require_revision(package_before, args.expect_package, "package")
        codex_version = _codex_version(args.codex_command)
        static_inputs = collect_static_inputs()
        candidate_docs = candidate_skill_inputs(candidate)
        candidate_contracts = candidate_contract_inputs(candidate)
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        print(f"preflight failed: {error}", file=sys.stderr)
        return 2

    output_root.mkdir(parents=True, exist_ok=False)
    provenance = {
        "schema_version": 1,
        "kind": "unity-cli-candidate-routing-acceptance",
        "created_at": _utc_now(),
        "case_count": 89,
        "eval_count": 8,
        "configuration": "current",
        "fresh_output_root": str(output_root),
        "live_unity_executed": False,
        "candidate": candidate_before,
        "package_companion": package_before,
        "expected_revisions": {
            "candidate": args.expect_candidate,
            "package": args.expect_package,
        },
        "executor": {
            "codex_version": codex_version,
            "codex_command": str(args.codex_command),
            "model": args.model,
            "reasoning_effort": args.reasoning,
            "approval_policy": "on-request",
            "approvals_reviewer": "auto_review",
            "windows_sandbox": "unelevated",
            "sandbox": "workspace-write",
            "ephemeral": True,
            "ignore_user_config": True,
            "ignore_rules": True,
            "timeout_seconds": args.timeout,
            "argv_template": _argv_template(
                args.codex_command,
                candidate,
                args.model,
                args.reasoning,
            ),
        },
        "environment": {
            "python": sys.version,
            "python_executable": sys.executable,
            "platform": platform.platform(),
            "runner_invocation": sys.argv,
        },
        "static_inputs": static_inputs,
        "candidate_skill_inputs": candidate_docs,
        "candidate_contract_inputs": candidate_contracts,
        "runs": [],
        "acceptance_passed": False,
    }
    provenance_path = output_root / "run-provenance.json"
    _write_json(provenance_path, provenance)

    for eval_item in evals:
        run = _run_one(
            eval_item,
            candidate,
            output_root,
            args.codex_command,
            args.model,
            args.reasoning,
            args.timeout,
            candidate_before,
        )
        provenance["runs"].append(run)
        _write_json(provenance_path, provenance)
        if not run["candidate_state_stable"]:
            break

    grader = _run_grader(output_root)
    grader_stdout_path = output_root / "grader.stdout.txt"
    grader_stderr_path = output_root / "grader.stderr.txt"
    grader_stdout_path.write_text(grader.stdout, encoding="utf-8")
    grader_stderr_path.write_text(grader.stderr, encoding="utf-8")
    candidate_after = git_snapshot(candidate)
    package_after = git_snapshot(package_repo)
    try:
        static_inputs_after = collect_static_inputs()
        static_inputs_stable = static_inputs_after == static_inputs
        static_inputs_error = None
    except OSError as error:
        static_inputs_after = {}
        static_inputs_stable = False
        static_inputs_error = f"{type(error).__name__}: {error}"
    summary_path = output_root / "summary.json"
    summary = _read_json(summary_path) if summary_path.is_file() else {}
    reports = {}
    for name in (
        "benchmark.json",
        "benchmark.md",
        "summary.json",
        "grader.stdout.txt",
        "grader.stderr.txt",
    ):
        path = output_root / name
        reports[name] = _hash_record(path) if path.is_file() else None

    revision_stable = (
        candidate_after["commit"] == candidate_before["commit"]
        and package_after["commit"] == package_before["commit"]
    )
    repositories_clean = candidate_after["clean"] and package_after["clean"]
    repository_state_stable = (
        candidate_after == candidate_before
        and package_after == package_before
    )
    all_runs_successful = (
        len(provenance["runs"]) == 8
        and all(run["successful"] for run in provenance["runs"])
    )
    strict_pass = (
        summary.get("passed") == 89
        and summary.get("total") == 89
        and summary.get("strict_micro_rate") == 1.0
    )
    provenance.update(
        {
            "completed_at": _utc_now(),
            "candidate_after": candidate_after,
            "package_companion_after": package_after,
            "revision_stable": revision_stable,
            "repositories_clean_after": repositories_clean,
            "repository_state_stable": repository_state_stable,
            "static_inputs_after": static_inputs_after,
            "static_inputs_stable": static_inputs_stable,
            "static_inputs_error": static_inputs_error,
            "grader": {
                "exit_code": grader.returncode,
                "stdout_sha256": _sha256(grader_stdout_path),
                "stderr_sha256": _sha256(grader_stderr_path),
            },
            "reports": reports,
            "strict_micro": {
                "passed": summary.get("passed"),
                "total": summary.get("total"),
                "rate": summary.get("strict_micro_rate"),
            },
            "acceptance_passed": (
                grader.returncode == 0
                and all_runs_successful
                and revision_stable
                and repositories_clean
                and repository_state_stable
                and static_inputs_stable
                and strict_pass
            ),
        }
    )
    _write_json(provenance_path, provenance)
    print(provenance_path)
    return 0 if provenance["acceptance_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
