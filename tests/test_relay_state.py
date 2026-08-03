"""Black-box coverage for the execution relay command-line lifecycle."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RELAY_SCRIPT = (
    REPOSITORY_ROOT
    / "skills"
    / "prepare-execution-handoff"
    / "scripts"
    / "relay_state.py"
)


class RelayStateBlackBoxTests(unittest.TestCase):
    """Exercise the CLI as a user would, without importing its implementation."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="relay-state-test-")
        self.repo = Path(self.tempdir.name)
        self._git("init", "--initial-branch=main")
        self._git("config", "user.name", "Relay State Tests")
        self._git("config", "user.email", "relay-state-tests@example.invalid")
        (self.repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
        self._git("add", "tracked.txt")
        self._git("commit", "-m", "baseline")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *args],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"git {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}",
        )
        return result

    def _run_cli(
        self, *args: str, expected_returncode: int = 0
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(RELAY_SCRIPT), *args],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            expected_returncode,
            msg=(
                f"relay_state.py {' '.join(args)} returned {result.returncode}, "
                f"expected {expected_returncode}\nstdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            ),
        )
        return result

    def _init_state(self, name: str = "state.json") -> Path:
        state = self.repo / name
        self._run_cli(
            "init",
            "--state",
            str(state),
            "--task-id",
            "relay-test",
            "--objective",
            "Exercise relay state gates",
            "--success",
            "Black-box lifecycle passes",
            "--scope",
            "tests",
            "--coordinator",
            "test-coordinator",
            "--repo",
            str(self.repo),
        )
        return state

    def _add_note(self, state: Path) -> None:
        self._run_cli(
            "note",
            "--state",
            str(state),
            "--kind",
            "file",
            "--subject",
            "tracked.txt",
            "--finding",
            "The repository has a tracked anchor candidate",
            "--certainty",
            "confirmed",
        )

    def _add_todo(self, state: Path, item_id: str, owner: str, scope: str) -> None:
        self._run_cli(
            "todo",
            "--state",
            str(state),
            "--item-id",
            item_id,
            "--action",
            f"Complete {item_id}",
            "--owner",
            owner,
            "--write-scope",
            scope,
            "--verify",
            "python3 -m unittest discover -s tests -v",
        )

    def _add_anchor(self, state: Path) -> None:
        (self.repo / "tracked.txt").write_text("anchored change\n", encoding="utf-8")
        self._run_cli(
            "anchor",
            "--state",
            str(state),
            "--path",
            "tracked.txt",
            "--rationale",
            "The tracked change proves the selected continuation point",
            "--verify-command",
            'python3 -c "raise SystemExit(0)"',
        )

    def _freeze(self, state: Path) -> subprocess.CompletedProcess[str]:
        return self._run_cli("freeze", "--state", str(state))

    def _make_frozen_state(
        self, *, item_id: str = "tests", owner: str = "worker", scope: str = "tests"
    ) -> Path:
        state = self._init_state()
        self._add_note(state)
        self._add_todo(state, item_id, owner, scope)
        self._add_anchor(state)
        self._freeze(state)
        return state

    def _write_receipt(
        self,
        state: Path,
        *,
        item_id: str,
        artifact_name: str,
        receipt_name: str,
    ) -> Path:
        artifact = self.repo / artifact_name
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(f"artifact for {item_id}\n", encoding="utf-8")
        receipt = self.repo / receipt_name
        state_data = json.loads(state.read_text(encoding="utf-8"))
        item = next(value for value in state_data["todos"] if value["id"] == item_id)
        self._run_cli(
            "receipt",
            "--state",
            str(state),
            "--item-id",
            item_id,
            "--worker",
            item["owner"],
            "--summary",
            f"Completed {item_id}",
            "--command",
            "python3 -m unittest discover -s tests -v",
            "--changed-path",
            artifact_name,
            "--artifact",
            artifact_name,
            "--output",
            str(receipt),
        )
        return receipt

    def test_successful_lifecycle_from_init_through_review(self) -> None:
        state = self._init_state()
        self._add_note(state)
        self._add_todo(state, "tests", "worker-tests", "tests")
        self._add_anchor(state)

        freeze = self._freeze(state)
        contract = json.loads(freeze.stdout)["contract_sha256"]

        brief = self._run_cli(
            "brief",
            "--state",
            str(state),
            "--owner",
            "worker-tests",
            "--format",
            "json",
        )
        brief_data = json.loads(brief.stdout)
        self.assertEqual(brief_data["contract_sha256"], contract)
        self.assertEqual([item["id"] for item in brief_data["assigned_items"]], ["tests"])

        receipt = self._write_receipt(
            state,
            item_id="tests",
            artifact_name="tests/results.txt",
            receipt_name="receipts/tests-receipt.json",
        )
        receipt_data = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(receipt_data["contract_sha256"], contract)
        self.assertEqual(receipt_data["exit_code"], 0)
        self.assertEqual(receipt_data["artifacts"][0]["path"], "tests/results.txt")

        review = self.repo / "review.json"
        self._run_cli(
            "review",
            "--state",
            str(state),
            "--reviewer",
            "reviewer",
            "--verdict",
            "pass",
            "--receipt",
            str(receipt),
            "--evidence",
            "The lifecycle receipt covers the only queued item and its artifact hash",
            "--verify-command",
            "python3 -m unittest discover -s tests -v",
            "--output",
            str(review),
        )
        review_data = json.loads(review.read_text(encoding="utf-8"))
        self.assertEqual(review_data["verdict"], "pass")
        self.assertEqual(review_data["contract_sha256"], contract)
        self.assertEqual([item["path"] for item in review_data["receipts"]], [str(receipt)])

    def test_freeze_rejects_overlapping_scopes_owned_by_different_workers(self) -> None:
        state = self._init_state()
        self._add_note(state)
        self._add_todo(state, "broad", "worker-a", "tests")
        self._add_todo(state, "nested", "worker-b", "tests/unit")
        self._add_anchor(state)

        check = self._run_cli("check", "--state", str(state), expected_returncode=1)
        check_data = json.loads(check.stdout)
        self.assertFalse(check_data["ready"])
        self.assertTrue(
            any("write scopes overlap across owners" in error for error in check_data["errors"])
        )

        freeze = self._run_cli("freeze", "--state", str(state), expected_returncode=2)
        self.assertIn("write scopes overlap across owners", freeze.stderr)
        state_data = json.loads(state.read_text(encoding="utf-8"))
        self.assertFalse(state_data["handoff"]["ready"])

    def test_all_draft_mutations_are_rejected_after_freeze(self) -> None:
        state = self._make_frozen_state()
        before = state.read_bytes()

        mutation_commands = (
            (
                "note",
                "--state",
                str(state),
                "--kind",
                "decision",
                "--subject",
                "post-freeze",
                "--finding",
                "must fail",
            ),
            (
                "todo",
                "--state",
                str(state),
                "--item-id",
                "late-item",
                "--action",
                "must fail",
                "--owner",
                "late-worker",
                "--write-scope",
                "docs",
                "--verify",
                "never",
            ),
            (
                "anchor",
                "--state",
                str(state),
                "--path",
                "tracked.txt",
                "--rationale",
                "must fail",
                "--verify-command",
                "never",
            ),
        )

        for command in mutation_commands:
            with self.subTest(command=command[0]):
                result = self._run_cli(*command, expected_returncode=2)
                self.assertIn("state is frozen", result.stderr)
                self.assertEqual(state.read_bytes(), before)

    def test_passing_review_requires_receipt_coverage_for_every_item(self) -> None:
        state = self._init_state()
        self._add_note(state)
        self._add_todo(state, "tests", "worker-tests", "tests")
        self._add_todo(state, "docs", "worker-docs", "docs")
        self._add_anchor(state)
        self._freeze(state)

        tests_receipt = self._write_receipt(
            state,
            item_id="tests",
            artifact_name="tests/result.txt",
            receipt_name="receipts/tests.json",
        )
        incomplete_review = self.repo / "incomplete-review.json"
        incomplete = self._run_cli(
            "review",
            "--state",
            str(state),
            "--reviewer",
            "reviewer",
            "--verdict",
            "pass",
            "--receipt",
            str(tests_receipt),
            "--evidence",
            "The second item is intentionally uncovered",
            "--verify-command",
            "python3 -m unittest discover -s tests -v",
            "--output",
            str(incomplete_review),
            expected_returncode=2,
        )
        self.assertIn("receipt coverage mismatch", incomplete.stderr)
        self.assertFalse(incomplete_review.exists())

        docs_receipt = self._write_receipt(
            state,
            item_id="docs",
            artifact_name="docs/result.txt",
            receipt_name="receipts/docs.json",
        )
        complete_review = self.repo / "complete-review.json"
        self._run_cli(
            "review",
            "--state",
            str(state),
            "--reviewer",
            "reviewer",
            "--verdict",
            "pass",
            "--receipt",
            str(tests_receipt),
            "--receipt",
            str(docs_receipt),
            "--evidence",
            "Both queued items have successful receipts",
            "--verify-command",
            "python3 -m unittest discover -s tests -v",
            "--output",
            str(complete_review),
        )
        review_data = json.loads(complete_review.read_text(encoding="utf-8"))
        self.assertEqual(review_data["verdict"], "pass")
        self.assertEqual(
            {Path(item["path"]).name for item in review_data["receipts"]},
            {"tests.json", "docs.json"},
        )

    def test_review_rejects_tampered_artifact_hash(self) -> None:
        state = self._make_frozen_state()
        receipt = self._write_receipt(
            state,
            item_id="tests",
            artifact_name="tests/result.txt",
            receipt_name="receipts/tests.json",
        )
        artifact = self.repo / "tests/result.txt"
        artifact.write_text("tampered after receipt\n", encoding="utf-8")

        review = self.repo / "tampered-review.json"
        result = self._run_cli(
            "review",
            "--state",
            str(state),
            "--reviewer",
            "reviewer",
            "--verdict",
            "pass",
            "--receipt",
            str(receipt),
            "--evidence",
            "The artifact must remain byte-identical after receipt creation",
            "--verify-command",
            "python3 -m unittest discover -s tests -v",
            "--output",
            str(review),
            expected_returncode=2,
        )
        self.assertIn("artifact hash mismatch: tests/result.txt", result.stderr)
        self.assertFalse(review.exists())

    def test_receipt_rejects_artifact_outside_owned_scope(self) -> None:
        state = self._make_frozen_state(scope="tests")
        artifact = self.repo / "outside.txt"
        artifact.write_text("outside owned scope\n", encoding="utf-8")
        receipt = self.repo / "outside-receipt.json"

        result = self._run_cli(
            "receipt",
            "--state",
            str(state),
            "--item-id",
            "tests",
            "--worker",
            "worker",
            "--summary",
            "must fail ownership validation",
            "--command",
            "python3 -m unittest discover -s tests -v",
            "--changed-path",
            "outside.txt",
            "--artifact",
            "outside.txt",
            "--output",
            str(receipt),
            expected_returncode=2,
        )
        self.assertIn("artifact is outside work-item ownership", result.stderr)
        self.assertFalse(receipt.exists())

    def test_anchor_executes_verification_and_rejects_a_false_success_claim(self) -> None:
        state = self._init_state()
        (self.repo / "tracked.txt").write_text("candidate change\n", encoding="utf-8")

        result = self._run_cli(
            "anchor",
            "--state",
            str(state),
            "--path",
            "tracked.txt",
            "--rationale",
            "verification must be executed",
            "--verify-command",
            'python3 -c "raise SystemExit(9)"',
            expected_returncode=2,
        )
        self.assertIn("anchor verification failed with exit 9", result.stderr)
        state_data = json.loads(state.read_text(encoding="utf-8"))
        self.assertIsNone(state_data["anchor"])

    def test_freeze_rejects_anchor_changed_after_verification(self) -> None:
        state = self._init_state()
        self._add_note(state)
        self._add_todo(state, "tests", "worker", "tests")
        self._add_anchor(state)
        (self.repo / "tracked.txt").write_text("changed after verification\n", encoding="utf-8")

        result = self._run_cli("freeze", "--state", str(state), expected_returncode=2)
        self.assertIn("anchor file hashes changed after verification", result.stderr)

    def test_frozen_read_only_evidence_is_rechecked(self) -> None:
        state = self._make_frozen_state(scope="tests")
        (self.repo / "tracked.txt").write_text("tampered frozen input\n", encoding="utf-8")

        result = self._run_cli("check", "--state", str(state), expected_returncode=1)
        report = json.loads(result.stdout)
        self.assertIn("frozen read-only input changed: tracked.txt", report["errors"])

    def test_review_rejects_worker_as_reviewer(self) -> None:
        state = self._make_frozen_state(owner="worker", scope="tests")
        receipt = self._write_receipt(
            state,
            item_id="tests",
            artifact_name="tests/result.txt",
            receipt_name="receipts/tests.json",
        )
        review = self.repo / "self-review.json"

        result = self._run_cli(
            "review",
            "--state",
            str(state),
            "--reviewer",
            "worker",
            "--verdict",
            "pass",
            "--receipt",
            str(receipt),
            "--evidence",
            "self-review must be rejected",
            "--verify-command",
            "python3 -m unittest discover -s tests -v",
            "--output",
            str(review),
            expected_returncode=2,
        )
        self.assertIn("reviewer identity must differ", result.stderr)
        self.assertFalse(review.exists())

    def test_review_rejects_noncanonical_forged_receipt_path(self) -> None:
        state = self._make_frozen_state(owner="worker", scope="tests")
        state_data = json.loads(state.read_text(encoding="utf-8"))
        forged = self.repo / "forged.json"
        forged.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "task_id": state_data["task_id"],
                    "item_id": "tests",
                    "worker": "worker",
                    "contract_sha256": state_data["handoff"]["contract_sha256"],
                    "commands": [
                        {
                            "command": "python3 -m unittest discover -s tests -v",
                            "exit_code": 0,
                        }
                    ],
                    "exit_code": 0,
                    "changes": [{"path": "tests/../tracked.txt", "sha256": "forged"}],
                    "artifacts": [{"path": "tests/../tracked.txt", "sha256": "forged"}],
                }
            ),
            encoding="utf-8",
        )

        result = self._run_cli(
            "review",
            "--state",
            str(state),
            "--reviewer",
            "reviewer",
            "--verdict",
            "pass",
            "--receipt",
            str(forged),
            "--evidence",
            "forged path must be rejected",
            "--verify-command",
            "python3 -m unittest discover -s tests -v",
            "--output",
            str(self.repo / "forged-review.json"),
            expected_returncode=2,
        )
        self.assertIn("traversal-free", result.stderr)

    def test_receipt_and_review_outputs_are_append_only(self) -> None:
        state = self._make_frozen_state(owner="worker", scope="tests")
        receipt = self._write_receipt(
            state,
            item_id="tests",
            artifact_name="tests/result.txt",
            receipt_name="receipts/tests.json",
        )
        duplicate = self._run_cli(
            "receipt",
            "--state",
            str(state),
            "--item-id",
            "tests",
            "--worker",
            "worker",
            "--summary",
            "duplicate must fail",
            "--command",
            "python3 -m unittest discover -s tests -v",
            "--changed-path",
            "tests/result.txt",
            "--artifact",
            "tests/result.txt",
            "--output",
            str(receipt),
            expected_returncode=2,
        )
        self.assertIn("refusing to overwrite existing evidence", duplicate.stderr)

        review = self.repo / "review.json"
        review_args = (
            "review",
            "--state",
            str(state),
            "--reviewer",
            "reviewer",
            "--verdict",
            "pass",
            "--receipt",
            str(receipt),
            "--evidence",
            "independent check",
            "--verify-command",
            "python3 -m unittest discover -s tests -v",
            "--output",
            str(review),
        )
        self._run_cli(*review_args)
        second_review = self._run_cli(*review_args, expected_returncode=2)
        self.assertIn("refusing to overwrite existing evidence", second_review.stderr)

    def test_passing_review_executes_independent_commands(self) -> None:
        state = self._make_frozen_state(owner="worker", scope="tests")
        receipt = self._write_receipt(
            state,
            item_id="tests",
            artifact_name="tests/result.txt",
            receipt_name="receipts/tests.json",
        )
        result = self._run_cli(
            "review",
            "--state",
            str(state),
            "--reviewer",
            "reviewer",
            "--verdict",
            "pass",
            "--receipt",
            str(receipt),
            "--evidence",
            "an extra failing review command must block acceptance",
            "--verify-command",
            "python3 -m unittest discover -s tests -v",
            "--verify-command",
            'python3 -c "raise SystemExit(5)"',
            "--output",
            str(self.repo / "failed-review.json"),
            expected_returncode=2,
        )
        self.assertIn("failed independent commands", result.stderr)


if __name__ == "__main__":
    unittest.main()
