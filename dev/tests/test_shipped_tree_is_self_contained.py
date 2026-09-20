"""Nothing in the shipped skill may reach back into the developer tree.

`npx skills add` copies the directory holding SKILL.md verbatim onto every
machine that installs this skill; it honours no ignore file, excluding only
.git and __pycache__. So the suite, the evals, the pinned fixture and the
refresh script live in dev/, and the shipped tree must not name them -- a
reference that survives here is a file the user will not have.

The tracked files are the shipped ones: the installer clones the repository,
so untracked scratch inside the skill directory never travels.
"""

import re
import subprocess
import unittest

from _paths import REPO_ROOT, SKILL_DIR

_DEV_ONLY = re.compile(
    r'(?<![\w/])dev/'
    r'|local_fixtures'
    r'|refresh_local_fixture'
    r'|(?<![\w/])evals/'
    r'|\btest_[a-z_]+\.py'
)


def _tracked_shipped_files():
    listing = subprocess.run(
        ["git", "ls-files", "--", str(SKILL_DIR.relative_to(REPO_ROOT))],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return [REPO_ROOT / line for line in listing.stdout.split("\n") if line]


class ShippedTreeIsSelfContained(unittest.TestCase):
    def test_no_shipped_file_names_the_developer_tree(self):
        offenders = []
        for path in _tracked_shipped_files():
            text = path.read_text("utf-8", errors="ignore")
            for number, line in enumerate(text.splitlines(), 1):
                if _DEV_ONLY.search(line):
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}"
                    )
        self.assertEqual([], offenders)

    def test_no_test_file_is_shipped(self):
        shipped_tests = [
            str(path.relative_to(REPO_ROOT))
            for path in _tracked_shipped_files()
            if path.name.startswith("test_") and path.suffix == ".py"
        ]
        self.assertEqual([], shipped_tests)
