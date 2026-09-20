"""Where the skill sits, for the developer-only suite that lives outside it.

The suite was moved out of ``skills/unity-cli/`` so it is not copied onto every
machine that installs the skill. That put a repository layout between each test
and the code it exercises, and a layout restated in fifteen files is a layout
that cannot be changed. It is stated here instead.

Importing this also puts ``scripts/`` on ``sys.path``, which is what makes
``from cli import ...`` work; unittest discovery has already put this directory
there, so tests need no bootstrap of their own.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_DIR = REPO_ROOT / "dev"
SKILL_DIR = REPO_ROOT / "skills" / "unity-cli"
SCRIPTS_DIR = SKILL_DIR / "scripts"
CLI_DIR = SCRIPTS_DIR / "cli"
CLI = CLI_DIR / "cs.py"
EVALS_DIR = DEV_DIR / "evals"
FIXTURE = DEV_DIR / "fixtures" / "builtin_registry_snapshot.v1.json"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
