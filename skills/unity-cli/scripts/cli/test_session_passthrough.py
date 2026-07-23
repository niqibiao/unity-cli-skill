import subprocess
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock


CLI_DIR = Path(__file__).resolve().parent
SCRIPTS_ROOT = CLI_DIR.parent
CS_PATH = CLI_DIR / "cs.py"

sys.path.insert(0, str(SCRIPTS_ROOT))

from cli import core_bridge, cs  # noqa: E402


class SessionPassthroughTests(unittest.TestCase):
    def test_exec_help_exposes_opt_in_session(self):
        result = subprocess.run(
            [sys.executable, str(CS_PATH), "exec", "--help"],
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertIn("--session", result.stdout)

    @mock.patch("cli.core_bridge.ConsoleSession")
    def test_new_session_forwards_explicit_session_id(self, console_session):
        args = SimpleNamespace(
            ip="127.0.0.1",
            port=13579,
            mode="editor",
            timeout=30,
            compile_ip=None,
            compile_port=None,
            session="agent-task-42",
        )
        project_root = Path("project")
        package_dir = Path("package")

        cs._new_session(project_root, args, package_dir)

        console_session.assert_called_once_with(
            project_root,
            args.ip,
            args.port,
            args.mode,
            args.timeout,
            pkg_dir=package_dir,
            compile_ip=args.compile_ip,
            compile_port=args.compile_port,
            session_id="agent-task-42",
        )

    @mock.patch("cli.core_bridge.ConsoleSession")
    def test_new_session_keeps_fresh_default(self, console_session):
        args = SimpleNamespace(
            ip="127.0.0.1",
            port=13579,
            mode="editor",
            timeout=30,
            compile_ip=None,
            compile_port=None,
        )

        cs._new_session(Path("project"), args, Path("package"))

        self.assertIsNone(console_session.call_args.kwargs["session_id"])

    def test_console_session_passes_explicit_id_to_core(self):
        client_base = SimpleNamespace(
            generate_session_id=mock.Mock(side_effect=lambda explicit: explicit or "fresh")
        )

        class SharedConfigState:
            runtime_dll_path = ""

            def current_server_base_url(self):
                return "http://127.0.0.1:13579"

            def current_mode_name(self):
                return "editor"

        fake_core = ModuleType("csharpconsole_core")
        fake_core.client_base = client_base
        fake_core.command_protocol = SimpleNamespace()
        fake_core.config_base = SimpleNamespace(SharedConfigState=SharedConfigState)
        fake_core.output = SimpleNamespace()
        fake_core.response_parser = SimpleNamespace()
        fake_core.transport_http = SimpleNamespace(post_json=mock.Mock())

        with mock.patch.dict(sys.modules, {"csharpconsole_core": fake_core}):
            with mock.patch.object(core_bridge, "_ensure_path"):
                session = core_bridge.ConsoleSession(
                    Path("project"),
                    pkg_dir=Path("package"),
                    session_id="agent-task-42",
                )

        client_base.generate_session_id.assert_called_once_with("agent-task-42")
        self.assertEqual("agent-task-42", session._session_id)


if __name__ == "__main__":
    unittest.main()
