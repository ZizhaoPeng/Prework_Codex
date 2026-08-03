"""Safety and deployment tests for the repository installer."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPOSITORY_ROOT / "install_skill.py"
SKILL_NAME = "prepare-execution-handoff"


class InstallSkillTests(unittest.TestCase):
    def _run(self, *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(INSTALLER), *args],
            cwd=REPOSITORY_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, expected, msg=result.stdout + result.stderr)
        return result

    def test_installs_into_codex_home_and_refuses_implicit_overwrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="execution-relay-install-") as temp:
            home = Path(temp) / "codex-home"
            target = home / "skills" / SKILL_NAME

            self._run("--codex-home", str(home))
            self.assertTrue((target / "SKILL.md").is_file())

            refused = self._run("--codex-home", str(home), expected=1)
            self.assertIn("use --force", refused.stderr)

    def test_force_replaces_directory_without_preserving_stale_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="execution-relay-force-") as temp:
            target = Path(temp) / SKILL_NAME
            self._run("--destination", str(target))
            stale = target / "stale.txt"
            stale.write_text("remove me\n", encoding="utf-8")

            self._run("--destination", str(target), "--force")
            self.assertFalse(stale.exists())
            self.assertTrue((target / "scripts" / "relay_state.py").is_file())

    def test_refuses_destination_that_contains_the_package_source(self) -> None:
        result = self._run("--destination", str(REPOSITORY_ROOT), "--force", expected=1)
        self.assertIn("separate from the bundled skill", result.stderr)

    def test_refuses_user_controlled_symlink_in_destination_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="execution-relay-link-") as temp:
            root = Path(temp).resolve()
            real_parent = root / "real"
            real_parent.mkdir()
            linked_parent = root / "linked"
            linked_parent.symlink_to(real_parent, target_is_directory=True)

            result = self._run(
                "--destination",
                str(linked_parent / SKILL_NAME),
                expected=1,
            )
            self.assertIn("destination symlink", result.stderr)


if __name__ == "__main__":
    unittest.main()
