"""Safety and provenance tests for the local candidate routing runner."""

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


CLI_DIR = Path(__file__).resolve().parent
SKILL_DIR = CLI_DIR.parent.parent
EVALS_DIR = SKILL_DIR / "evals"
REPO_DIR = SKILL_DIR.parents[1]

SPEC = importlib.util.spec_from_file_location(
    "unity_cli_routing_runner",
    EVALS_DIR / "run_routing_current.py",
)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)

DISCOVERY_OUTPUT = (
    '{"ok":true,"summary":"Listed routes","data":'
    '{"schemaVersion":1,"kind":"route-cards","routes":[]}}'
)


def _trace_with_command(command, output=DISCOVERY_OUTPUT, exit_code=0):
    return [
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": command,
                "aggregated_output": output,
                "exit_code": exit_code,
            },
        },
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": '{"results":[]}'},
        },
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    ]


class RoutingRunnerTests(unittest.TestCase):
    def test_trace_allows_only_completed_transient_reconnect_errors(self):
        candidate = Path(r"E:\frozen-candidate")
        command = (
            'python -B "skills/unity-cli/scripts/cli/cs.py" '
            "list-commands --offline --domain editor --tier core --json"
        )
        transient = _trace_with_command(command)
        transient.insert(
            0,
            {
                "type": "error",
                "message": (
                    "Reconnecting... 2/5 (stream disconnected before "
                    "completion: tls handshake eof)"
                ),
            },
        )
        recovered = RUNNER.validate_trace(transient, candidate)

        self.assertTrue(recovered["safe"], recovered["violations"])
        self.assertEqual(1, recovered["transient_reconnections"])

        fatal = _trace_with_command(command)
        fatal.insert(
            0,
            {"type": "error", "message": "invalid response schema"},
        )
        rejected = RUNNER.validate_trace(fatal, candidate)
        self.assertFalse(rejected["safe"])
        self.assertEqual(0, rejected["transient_reconnections"])

    def test_trace_accepts_doc_reads_and_bundled_offline_discovery(self):
        candidate = Path(r"E:\frozen-candidate")
        events = [
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "Get-Content skills/unity-cli/SKILL.md",
                    "aggregated_output": "skill",
                    "exit_code": 0,
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": (
                        'python -B "skills/unity-cli/scripts/cli/cs.py" '
                        "list-commands --offline --domain editor --tier core --json"
                    ),
                    "aggregated_output": DISCOVERY_OUTPUT,
                    "exit_code": 0,
                },
            },
            {
                "type": "item.completed",
                "item": {"type": "reasoning", "text": "routing"},
            },
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": '{"results":[]}'},
            },
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        ]

        result = RUNNER.validate_trace(events, candidate)

        self.assertTrue(result["safe"])
        self.assertEqual([], result["violations"])
        self.assertEqual(2, result["tool_calls"])
        self.assertEqual(1, result["offline_discovery_calls"])
        self.assertEqual(1, result["turn_completed"])
        self.assertEqual(15, result["total_tokens"])

    def test_trace_rejects_non_offline_mutating_leaking_and_network_commands(self):
        candidate = Path(r"E:\frozen-candidate")
        unsafe_commands = (
            (
                'python "skills/unity-cli/scripts/cli/cs.py" '
                "list-commands --domain editor --json"
            ),
            (
                'python "skills/unity-cli/scripts/cli/cs.py" '
                "command --input request.json --json"
            ),
            (
                'python -c "print(1)" "skills/unity-cli/scripts/cli/cs.py" '
                "list-commands --offline --json"
            ),
            "Get-Content skills/unity-cli/evals/route_cases.json",
            "Get-Content reports/pr12-complex-workflow-eval/evals.json",
            "Set-Content answer.json '{}'",
            "curl https://example.com",
            r"Get-Content E:\UnityProjects\another-worktree\skills\unity-cli\SKILL.md",
            r"Get-Content skills/unity-cli/SKILL.md ..\another-worktree\secret.md",
            'rg -n "command" skills/unity-cli/SKILL.md .',
            "Get-Content user-secrets.txt skills/unity-cli/SKILL.md",
            "Get-Content skills/unity-cli/SKILL.md & Get-Content AGENTS.md",
            (
                r'"C:\tools\evil.exe" -Command '
                r'"Get-Content skills/unity-cli/SKILL.md"'
            ),
            (
                r'"C:\tools\pwsh.exe" -Command '
                r'"Get-Content skills/unity-cli/SKILL.md"'
            ),
            (
                'python -B "..\\another-worktree\\skills\\unity-cli\\scripts'
                '\\cli\\cs.py" list-commands --offline --domain editor --json'
            ),
        )

        for command in unsafe_commands:
            with self.subTest(command=command):
                result = RUNNER.validate_trace(
                    _trace_with_command(command), candidate
                )
                self.assertFalse(result["safe"])
                self.assertTrue(result["violations"])

    def test_markdown_reads_validate_every_resolved_target(self):
        candidate = Path(r"E:\frozen-candidate")
        allowed = (
            "Get-Content -Raw skills/unity-cli/SKILL.md",
            "Get-Content -LiteralPath skills/unity-cli/references/commands.md",
            "Get-Content AGENTS.md CLAUDE.md",
        )
        rejected = (
            "Get-Content skills/unity-cli/SKILL.md user-secrets.txt",
            (
                "Get-Content skills/unity-cli/references/commands.md "
                "skills/unity-cli/references/../../private.md"
            ),
            "Get-Content skills/unity-cli/references/*.md",
            "Get-Content -TotalCount 1 skills/unity-cli/SKILL.md",
        )

        for command in allowed:
            with self.subTest(command=command):
                result = RUNNER.validate_trace(
                    _trace_with_command(command), candidate
                )
                self.assertTrue(result["safe"], result["violations"])
        for command in rejected:
            with self.subTest(command=command):
                result = RUNNER.validate_trace(
                    _trace_with_command(command), candidate
                )
                self.assertFalse(result["safe"])
                self.assertTrue(result["violations"])

    def test_trace_requires_one_completed_turn_and_known_completed_item_types(self):
        candidate = Path(r"E:\frozen-candidate")
        events = [
            {
                "type": "item.completed",
                "item": {"type": "web_search", "query": "unity-cli"},
            }
        ]

        result = RUNNER.validate_trace(events, candidate)

        self.assertFalse(result["safe"])
        self.assertTrue(
            any("turn.completed" in item for item in result["violations"])
        )
        self.assertTrue(
            any("item type" in item for item in result["violations"])
        )

    # The runner resolves a traced path against the candidate checkout on the
    # machine that produced the trace, so an absolute path only means what it
    # says on that machine's filesystem. Every other case here uses a path
    # relative to the candidate and runs everywhere.
    @unittest.skipUnless(
        os.name == "nt",
        "the case pins absolute Windows paths against a Windows checkout",
    )
    def test_trace_accepts_exact_windows_wrapper_and_todo_without_tool_count(self):
        candidate = Path(
            r"E:\UnityProjects\_unity-cli-pr12-complex-eval\candidate-pr12"
        )
        command = (
            r'"C:\Program Files\PowerShell\7\pwsh.exe" -Command '
            r'"python -B '
            r"'E:\UnityProjects\_unity-cli-pr12-complex-eval\candidate-pr12"
            r"\skills\unity-cli\scripts\cli\cs.py' "
            r'list-commands --offline --domain editor --tier core --json"'
        )
        for traced_command in (command, command.replace("\\", "\\\\")):
            events = _trace_with_command(traced_command)
            events.insert(
                1,
                {
                    "type": "item.completed",
                    "item": {
                        "type": "todo_list",
                        "items": [{"text": "route cases", "completed": True}],
                    },
                },
            )

            result = RUNNER.validate_trace(events, candidate)
            with self.subTest(traced_command=traced_command):
                self.assertTrue(result["safe"], result["violations"])
                self.assertEqual(1, result["tool_calls"])
                self.assertEqual(1, result["offline_discovery_calls"])

    def test_trace_rejects_plain_python_without_no_bytecode_flag(self):
        command = (
            'python "skills/unity-cli/scripts/cli/cs.py" '
            "list-commands --offline --domain editor --tier core --json"
        )

        result = RUNNER.validate_trace(
            _trace_with_command(command),
            Path(r"E:\frozen-candidate"),
        )

        self.assertFalse(result["safe"])
        self.assertTrue(
            any("literal -B" in violation for violation in result["violations"])
        )

    def test_discovery_evidence_requires_successful_nonempty_output(self):
        candidate = Path(r"E:\frozen-candidate")
        command = (
            'python -B "skills/unity-cli/scripts/cli/cs.py" '
            "list-commands --offline --domain objects --tier core --json"
        )
        success = RUNNER.validate_trace(
            _trace_with_command(command, output=DISCOVERY_OUTPUT),
            candidate,
        )
        empty = RUNNER.validate_trace(
            _trace_with_command(command, output=""),
            candidate,
        )
        failed = RUNNER.validate_trace(
            _trace_with_command(command, output="failure", exit_code=1),
            candidate,
        )

        self.assertEqual(1, success["offline_discovery_calls"])
        self.assertTrue(RUNNER.discovery_evidence_satisfied(2, success))
        self.assertEqual(0, empty["offline_discovery_calls"])
        self.assertFalse(RUNNER.discovery_evidence_satisfied(2, empty))
        self.assertEqual(0, failed["offline_discovery_calls"])
        self.assertFalse(RUNNER.discovery_evidence_satisfied(2, failed))

    def test_discovery_rejects_lowercase_flag_help_and_fake_output(self):
        candidate = Path(r"E:\frozen-candidate")
        lowercase = (
            'python -b "skills/unity-cli/scripts/cli/cs.py" '
            "list-commands --offline --domain objects --tier core --json"
        )
        help_only = (
            'python -B "skills/unity-cli/scripts/cli/cs.py" '
            "list-commands --offline --domain objects --tier core --help --json"
        )
        valid_command = (
            'python -B "skills/unity-cli/scripts/cli/cs.py" '
            "list-commands --offline --domain objects --tier core --json"
        )

        lowercase_trace = RUNNER.validate_trace(
            _trace_with_command(lowercase),
            candidate,
        )
        help_trace = RUNNER.validate_trace(
            _trace_with_command(help_only, output="usage: cs list-commands"),
            candidate,
        )
        fake_trace = RUNNER.validate_trace(
            _trace_with_command(valid_command, output='{"commands":[]}'),
            candidate,
        )
        boolean_version = RUNNER.validate_trace(
            _trace_with_command(
                valid_command,
                output=DISCOVERY_OUTPUT.replace(
                    '"schemaVersion":1',
                    '"schemaVersion":true',
                ),
            ),
            candidate,
        )
        float_version = RUNNER.validate_trace(
            _trace_with_command(
                valid_command,
                output=DISCOVERY_OUTPUT.replace(
                    '"schemaVersion":1',
                    '"schemaVersion":1.0',
                ),
            ),
            candidate,
        )

        self.assertFalse(lowercase_trace["safe"])
        self.assertEqual(0, lowercase_trace["offline_discovery_calls"])
        self.assertFalse(help_trace["safe"])
        self.assertEqual(0, help_trace["offline_discovery_calls"])
        self.assertTrue(fake_trace["safe"], fake_trace["violations"])
        self.assertEqual(0, fake_trace["offline_discovery_calls"])
        self.assertEqual(0, boolean_version["offline_discovery_calls"])
        self.assertEqual(0, float_version["offline_discovery_calls"])

    def test_editor_evidence_requires_core_and_advanced_queries(self):
        candidate = Path(r"E:\frozen-candidate")
        core = (
            'python -B "skills/unity-cli/scripts/cli/cs.py" '
            "list-commands --offline --domain editor --tier core --json"
        )
        advanced = (
            r'"C:\Program Files\PowerShell\7\pwsh.exe" -Command '
            r'"python -B '
            r"'skills/unity-cli/scripts/cli/cs.py' "
            r'list-commands --offline --domain editor --tier advanced --json"'
        )
        events = _trace_with_command(core)
        events.insert(1, _trace_with_command(advanced)[0])
        result = RUNNER.validate_trace(events, candidate)

        self.assertTrue(result["safe"], result["violations"])
        self.assertEqual(2, result["offline_discovery_calls"])
        self.assertTrue(RUNNER.discovery_evidence_satisfied(1, result))

    def test_final_file_must_match_the_last_completed_agent_message(self):
        events = _trace_with_command(
            "Get-Content skills/unity-cli/SKILL.md"
        )

        self.assertTrue(
            RUNNER.final_matches_trace(events, ' \n{"results":[]}\n')
        )
        self.assertFalse(
            RUNNER.final_matches_trace(events, '{"results":[{"id":"extra"}]}')
        )

    def test_static_hashes_cover_every_oracle_schema_and_contract_input(self):
        inputs = RUNNER.collect_static_inputs()

        self.assertEqual(
            {
                "evals",
                "route_oracle",
                "trigger_oracle",
                "registry_snapshot",
                "routing_overlay",
                "route_schema",
                "trigger_schema",
                "grader",
                "runner",
            },
            set(inputs),
        )
        for item in inputs.values():
            self.assertTrue(Path(item["path"]).is_file())
            self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")

    def test_candidate_inputs_hash_agent_docs_and_offline_contract_sources(self):
        skill_inputs = RUNNER.candidate_skill_inputs(REPO_DIR)
        contract_inputs = RUNNER.candidate_contract_inputs(REPO_DIR)

        self.assertIn("skills/unity-cli/SKILL.md", skill_inputs)
        self.assertTrue(
            any(
                path.startswith("skills/unity-cli/references/")
                for path in skill_inputs
            )
        )
        self.assertEqual(
            {"registry_snapshot", "routing_overlay"},
            set(contract_inputs),
        )
        for item in (*skill_inputs.values(), *contract_inputs.values()):
            self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")

    def test_output_root_must_be_new_and_outside_frozen_repositories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            candidate = root / "candidate"
            package = root / "package"
            candidate.mkdir()
            package.mkdir()

            RUNNER.validate_output_path(root / "fresh", candidate, package)
            with self.assertRaisesRegex(ValueError, "already exists"):
                RUNNER.validate_output_path(candidate, candidate, package)
            with self.assertRaisesRegex(ValueError, "outside"):
                RUNNER.validate_output_path(candidate / "results", candidate, package)

    def test_ignored_content_fingerprint_detects_workspace_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            (repository / ".gitignore").write_text(
                "__pycache__/\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "-C", str(repository), "add", ".gitignore"],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "-c",
                    "user.name=Evaluator",
                    "-c",
                    "user.email=evaluator@example.invalid",
                    "commit",
                    "-q",
                    "-m",
                    "fixture",
                ],
                check=True,
            )
            ignored = repository / "__pycache__"
            ignored.mkdir()
            artifact = ignored / "probe.pyc"
            artifact.write_bytes(b"before")
            before = RUNNER.git_snapshot(repository)
            artifact.write_bytes(b"after")
            after = RUNNER.git_snapshot(repository)

            self.assertTrue(before["clean"])
            self.assertTrue(after["clean"])
            self.assertNotEqual(
                before["ignored_state"],
                after["ignored_state"],
            )

    def test_codex_command_is_ephemeral_guarded_and_writes_last_message(self):
        candidate = Path(r"E:\frozen-candidate")
        schema = EVALS_DIR / "routing-output.schema.json"
        output = Path(r"E:\results\route.json")

        command = RUNNER.build_codex_command(
            Path(r"C:\tools\codex.cmd"),
            candidate,
            schema,
            output,
            "gpt-5.4",
            "medium",
        )

        self.assertIn("--ephemeral", command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertEqual(
            "on-request",
            command[command.index("--ask-for-approval") + 1],
        )
        self.assertIn('approvals_reviewer="auto_review"', command)
        self.assertIn('windows.sandbox="unelevated"', command)
        self.assertEqual(
            "workspace-write",
            command[command.index("--sandbox") + 1],
        )
        self.assertEqual(str(schema), command[command.index("--output-schema") + 1])
        self.assertEqual(
            str(output), command[command.index("--output-last-message") + 1]
        )
        self.assertEqual(str(candidate), command[command.index("-C") + 1])

    def test_schema_selection_and_case_count_are_fixed_for_eight_evals(self):
        evals = RUNNER.load_evals()

        self.assertEqual(8, len(evals))
        self.assertEqual(89, RUNNER.case_count(evals))
        self.assertEqual(
            EVALS_DIR / "routing-trigger-output.schema.json",
            RUNNER.schema_for_eval(evals[-1]),
        )
        self.assertTrue(
            all(
                RUNNER.schema_for_eval(item)
                == EVALS_DIR / "routing-output.schema.json"
                for item in evals[:-1]
            )
        )


if __name__ == "__main__":
    unittest.main()
