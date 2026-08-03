#!/usr/bin/env python3
"""Create and verify evidence-backed Prework_Codex continuation contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
EVIDENCE_KINDS = ("file", "command", "decision", "rejected", "risk")
CERTAINTY_LEVELS = ("confirmed", "provisional")


class RelayError(RuntimeError):
    """Raised when a relay operation violates the contract."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def digest_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise RelayError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.rstrip("\n")


def repository_changed_paths(root: Path) -> list[str]:
    tracked = run_git(root, "diff", "--name-only", "-z", "HEAD", "--")
    untracked = run_git(root, "ls-files", "--others", "--exclude-standard", "-z")
    return sorted({value for value in (tracked + untracked).split("\0") if value})


def repository_snapshot(requested_root: Path) -> dict[str, Any]:
    root = Path(run_git(requested_root.resolve(), "rev-parse", "--show-toplevel"))
    status = run_git(root, "status", "--porcelain=v1", "-z")
    return {
        "root": str(root),
        "branch": run_git(root, "branch", "--show-current"),
        "head": run_git(root, "rev-parse", "HEAD"),
        "initial_status_sha256": hashlib.sha256(status.encode()).hexdigest(),
        "initial_changed_paths": repository_changed_paths(root),
    }


def load_state(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RelayError(f"state file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RelayError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(state, dict):
        raise RelayError("state root must be an object")
    return state


def save_json(path: Path, value: Any, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if exclusive:
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as exc:
            raise RelayError(f"refusing to overwrite existing evidence: {path}") from exc
        return
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(payload)
        temp_name = handle.name
    os.replace(temp_name, path)


def require_draft(state: dict[str, Any]) -> None:
    if state.get("handoff", {}).get("ready"):
        raise RelayError("state is frozen; create a new state version instead of mutating it")


def append_event(state: dict[str, Any], event_type: str, payload: Any) -> None:
    events = state.setdefault("events", [])
    events.append(
        {
            "sequence": len(events) + 1,
            "at": utc_now(),
            "type": event_type,
            "payload_sha256": digest_value(payload),
        }
    )


def contract_projection(state: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "schema_version",
        "task_id",
        "state_file",
        "objective",
        "success_criteria",
        "constraints",
        "repository",
        "coordinator",
        "evidence",
        "todos",
        "anchor",
    )
    return {key: state.get(key) for key in keys}


def normalize_scope(value: str) -> str:
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise RelayError(f"write scope escapes repository: {value}")
    normalized = candidate.as_posix().strip("/")
    if not normalized or normalized == ".":
        return "."
    return normalized


def resolve_repo_file(root: Path, value: str) -> tuple[Path, str]:
    raw = Path(value)
    if raw.is_absolute() or ".." in raw.parts:
        raise RelayError(f"repository path must be relative and traversal-free: {value}")
    candidate = (root / raw).resolve()
    try:
        relative = candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise RelayError(f"path escapes repository: {value}") from exc
    return candidate, relative


def repo_relative_if_inside(root: Path, value: Path) -> str | None:
    try:
        return value.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def run_checked_command(root: Path, command: str, timeout: int) -> dict[str, Any]:
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise RelayError(f"invalid command syntax: {command!r}: {exc}") from exc
    if not argv:
        raise RelayError("verification command must not be empty")
    try:
        result = subprocess.run(
            argv,
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
        return {
            "command": command,
            "argv": argv,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "output_sha256": digest_value(
                {"stdout": result.stdout, "stderr": result.stderr, "exit_code": result.returncode}
            ),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return {
            "command": command,
            "argv": argv,
            "exit_code": 124,
            "stdout": stdout,
            "stderr": stderr,
            "output_sha256": digest_value(
                {"stdout": stdout, "stderr": stderr, "exit_code": 124}
            ),
            "timed_out": True,
        }
    except FileNotFoundError as exc:
        return {
            "command": command,
            "argv": argv,
            "exit_code": 127,
            "stdout": "",
            "stderr": str(exc),
            "output_sha256": digest_value({"stdout": "", "stderr": str(exc), "exit_code": 127}),
            "timed_out": False,
        }


def scopes_overlap(left: str, right: str) -> bool:
    if left == "." or right == ".":
        return True
    left_parts = Path(left).parts
    right_parts = Path(right).parts
    shortest = min(len(left_parts), len(right_parts))
    return left_parts[:shortest] == right_parts[:shortest]


def path_in_scope(path: str, scope: str) -> bool:
    if scope == ".":
        return True
    path_parts = Path(path).parts
    scope_parts = Path(scope).parts
    return len(path_parts) >= len(scope_parts) and path_parts[: len(scope_parts)] == scope_parts


def command_names(records: list[Any]) -> set[str]:
    return {
        str(record.get("command", "")) if isinstance(record, dict) else str(record)
        for record in records
    }


def validate_receipt_against_item(
    receipt: dict[str, Any], item: dict[str, Any], root: Path
) -> None:
    if receipt.get("worker") != item.get("owner"):
        raise RelayError(
            f"receipt worker {receipt.get('worker')!r} does not match owner {item.get('owner')!r}"
        )
    commands = command_names(receipt.get("commands", []))
    missing_commands = [value for value in item.get("verification", []) if value not in commands]
    if missing_commands:
        raise RelayError(f"receipt omits required verification commands: {missing_commands}")
    scopes = item.get("write_scopes", [])
    for field in ("artifacts", "changes"):
        for record in receipt.get(field, []):
            record_path = str(record.get("path", ""))
            _, normalized = resolve_repo_file(root, record_path)
            if normalized != record_path:
                raise RelayError(f"receipt {field} path is not canonical: {record_path!r}")
            if any(path_in_scope(normalized, scope) for scope in scopes):
                continue
            raise RelayError(
                f"{field[:-1]} is outside work-item ownership: {normalized!r}; scopes={scopes}"
            )
    if not receipt.get("changes"):
        raise RelayError("receipt must record at least one changed path")


def change_records(root: Path, values: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    changed = set(repository_changed_paths(root))
    for value in values:
        candidate, relative = resolve_repo_file(root, value)
        if relative in seen:
            raise RelayError(f"duplicate changed path: {relative}")
        if relative not in changed:
            raise RelayError(f"path is not changed relative to HEAD: {relative}")
        seen.add(relative)
        records.append(
            {
                "path": relative,
                "sha256": digest_file(candidate) if candidate.is_file() else None,
                "deleted": not candidate.exists(),
                "diff_sha256": hashlib.sha256(
                    run_git(root, "diff", "--binary", "HEAD", "--", relative).encode()
                ).hexdigest(),
            }
        )
    return records


def anchor_current_errors(state: dict[str, Any]) -> list[str]:
    anchor = state.get("anchor")
    if not isinstance(anchor, dict):
        return []
    root = Path(state["repository"]["root"])
    errors: list[str] = []
    current_hashes: dict[str, str] = {}
    for value in anchor.get("paths", []):
        try:
            candidate, relative = resolve_repo_file(root, value)
        except RelayError as exc:
            errors.append(str(exc))
            continue
        if candidate.is_file():
            current_hashes[relative] = digest_file(candidate)
    current_status = run_git(root, "status", "--porcelain=v1", "--", *anchor.get("paths", []))
    current_diff = run_git(
        root, "diff", "--binary", "--no-ext-diff", "--", *anchor.get("paths", [])
    )
    if current_hashes != anchor.get("file_sha256", {}):
        errors.append("anchor file hashes changed after verification")
    if current_status != anchor.get("path_status"):
        errors.append("anchor path status changed after verification")
    if hashlib.sha256(current_diff.encode()).hexdigest() != anchor.get("diff_sha256"):
        errors.append("anchor diff changed after verification")
    return errors


def frozen_input_errors(state: dict[str, Any]) -> list[str]:
    if not state.get("handoff", {}).get("ready"):
        return []
    root = Path(state["repository"]["root"])
    errors: list[str] = []
    if run_git(root, "rev-parse", "HEAD") != state["repository"].get("head"):
        errors.append("repository HEAD changed after the relay baseline")
    scopes = [scope for item in state.get("todos", []) for scope in item.get("write_scopes", [])]
    immutable: dict[str, str] = {}
    for item in state.get("evidence", []):
        if item.get("kind") != "file" or not item.get("artifact_sha256"):
            continue
        _, relative = resolve_repo_file(root, str(item.get("subject", "")))
        if not any(path_in_scope(relative, scope) for scope in scopes):
            immutable[relative] = str(item["artifact_sha256"])
    for relative, expected in state.get("anchor", {}).get("file_sha256", {}).items():
        if not any(path_in_scope(relative, scope) for scope in scopes):
            immutable[relative] = expected
    for relative, expected in immutable.items():
        candidate, _ = resolve_repo_file(root, relative)
        if not candidate.is_file() or digest_file(candidate) != expected:
            errors.append(f"frozen read-only input changed: {relative}")
    return errors


def validate_state(state: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if state.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if not str(state.get("task_id", "")).strip():
        errors.append("task_id is required")
    if not str(state.get("objective", "")).strip():
        errors.append("objective is required")
    if not state.get("success_criteria"):
        errors.append("at least one success criterion is required")

    evidence = state.get("evidence", [])
    if not any(item.get("kind") in {"file", "command"} for item in evidence):
        errors.append("record at least one file or command observation")
    if any(item.get("certainty") == "provisional" for item in evidence):
        warnings.append("provisional evidence remains; include it in the worker brief")

    todos = state.get("todos", [])
    if not todos:
        errors.append("at least one todo item is required")
    seen_ids: set[str] = set()
    for item in todos:
        item_id = str(item.get("id", ""))
        if not item_id or item_id in seen_ids:
            errors.append(f"todo id is missing or duplicated: {item_id!r}")
        seen_ids.add(item_id)
        if not item.get("write_scopes"):
            errors.append(f"todo {item_id!r} needs a write scope")
        if not item.get("verification"):
            errors.append(f"todo {item_id!r} needs a verification command")

    for index, left in enumerate(todos):
        for right in todos[index + 1 :]:
            if left.get("owner") == right.get("owner"):
                continue
            for left_scope in left.get("write_scopes", []):
                for right_scope in right.get("write_scopes", []):
                    if scopes_overlap(left_scope, right_scope):
                        errors.append(
                            f"write scopes overlap across owners: {left_scope!r} and {right_scope!r}"
                        )

    anchor = state.get("anchor")
    if not isinstance(anchor, dict):
        errors.append("record a first verified code change before freezing")
    else:
        if not anchor.get("paths"):
            errors.append("anchor needs at least one changed path")
        if anchor.get("verification", {}).get("exit_code") != 0:
            errors.append("anchor verification must have exit code 0")
        if not anchor.get("path_status"):
            errors.append("anchor paths are not changed relative to the repository baseline")
        if not anchor.get("file_sha256"):
            errors.append("anchor needs hashes for at least one changed file")

    handoff = state.get("handoff", {})
    if handoff.get("ready"):
        expected = digest_value(contract_projection(state))
        if handoff.get("contract_sha256") != expected:
            errors.append("frozen contract digest does not match current state")
    return errors, warnings


def cmd_init(args: argparse.Namespace) -> None:
    path = Path(args.state)
    if path.exists():
        raise RelayError(f"refusing to overwrite existing state: {path}")
    state: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "task_id": args.task_id,
        "state_file": str(path.resolve()),
        "objective": args.objective,
        "success_criteria": args.success,
        "constraints": args.constraint,
        "repository": repository_snapshot(Path(args.repo)),
        "coordinator": args.coordinator,
        "evidence": [],
        "todos": [],
        "anchor": None,
        "handoff": {"ready": False, "frozen_at": None, "contract_sha256": None},
        "events": [],
    }
    append_event(state, "initialized", {"scope": args.scope})
    if args.scope:
        state["constraints"].append("Requested scope: " + ", ".join(args.scope))
    save_json(path, state, exclusive=True)
    print(path)


def cmd_note(args: argparse.Namespace) -> None:
    path = Path(args.state)
    state = load_state(path)
    require_draft(state)
    note: dict[str, Any] = {
        "kind": args.kind,
        "subject": args.subject,
        "finding": args.finding,
        "evidence": args.evidence,
        "certainty": args.certainty,
        "recorded_at": utc_now(),
    }
    if args.kind == "file":
        root = Path(state["repository"]["root"])
        observed, relative = resolve_repo_file(root, args.subject)
        if not observed.is_file():
            raise RelayError(f"observed file does not exist: {observed}")
        note["subject"] = relative
        note["artifact_sha256"] = digest_file(observed)
    state["evidence"].append(note)
    append_event(state, "evidence-added", note)
    save_json(path, state)


def cmd_todo(args: argparse.Namespace) -> None:
    path = Path(args.state)
    state = load_state(path)
    require_draft(state)
    if any(item.get("id") == args.item_id for item in state["todos"]):
        raise RelayError(f"duplicate todo id: {args.item_id}")
    item = {
        "id": args.item_id,
        "action": args.action,
        "owner": args.owner,
        "write_scopes": [normalize_scope(value) for value in args.write_scope],
        "verification": args.verify,
        "depends_on": args.depends_on,
        "status": "pending",
    }
    state["todos"].append(item)
    append_event(state, "todo-added", item)
    save_json(path, state)


def cmd_anchor(args: argparse.Namespace) -> None:
    path = Path(args.state)
    state = load_state(path)
    require_draft(state)
    root = Path(state["repository"]["root"])
    normalized_paths = [normalize_scope(value) for value in args.path]
    verification = run_checked_command(root, args.verify_command, args.command_timeout)
    if verification["exit_code"] != 0:
        raise RelayError(
            f"anchor verification failed with exit {verification['exit_code']}: "
            f"{args.verify_command}\n{verification['stderr']}"
        )
    hashes: dict[str, str] = {}
    for value in normalized_paths:
        candidate, relative = resolve_repo_file(root, value)
        if candidate.is_file():
            hashes[relative] = digest_file(candidate)
    path_status = run_git(root, "status", "--porcelain=v1", "--", *normalized_paths)
    diff = run_git(root, "diff", "--binary", "--no-ext-diff", "--", *normalized_paths)
    anchor = {
        "paths": normalized_paths,
        "rationale": args.rationale,
        "verification": verification,
        "file_sha256": hashes,
        "path_status": path_status,
        "diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
        "recorded_at": utc_now(),
    }
    state["anchor"] = anchor
    append_event(state, "anchor-recorded", anchor)
    save_json(path, state)


def cmd_check(args: argparse.Namespace) -> None:
    state = load_state(Path(args.state))
    errors, warnings = validate_state(state)
    if state.get("handoff", {}).get("ready"):
        errors.extend(frozen_input_errors(state))
    else:
        errors.extend(anchor_current_errors(state))
    report = {
        "ready": not errors and bool(state.get("handoff", {}).get("ready")),
        "frozen": bool(state.get("handoff", {}).get("ready")),
        "errors": errors,
        "warnings": warnings,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    if errors:
        raise SystemExit(1)


def cmd_freeze(args: argparse.Namespace) -> None:
    path = Path(args.state)
    state = load_state(path)
    require_draft(state)
    errors, warnings = validate_state(state)
    errors.extend(anchor_current_errors(state))
    if errors:
        raise RelayError("cannot freeze:\n- " + "\n- ".join(errors))
    state["handoff"] = {
        "ready": True,
        "frozen_at": utc_now(),
        "contract_sha256": digest_value(contract_projection(state)),
    }
    append_event(state, "contract-frozen", state["handoff"])
    save_json(path, state)
    print(json.dumps({"contract_sha256": state["handoff"]["contract_sha256"], "warnings": warnings}, indent=2))


def cmd_brief(args: argparse.Namespace) -> None:
    state = load_state(Path(args.state))
    errors, _ = validate_state(state)
    errors.extend(frozen_input_errors(state))
    if errors or not state.get("handoff", {}).get("ready"):
        raise RelayError("worker brief requires a valid frozen state")
    items = [item for item in state["todos"] if not args.owner or item["owner"] == args.owner]
    if args.owner and not items:
        raise RelayError(f"no work items assigned to owner {args.owner!r}")
    if args.format == "json":
        print(json.dumps({"contract_sha256": state["handoff"]["contract_sha256"], "state": state, "assigned_items": items}, indent=2, sort_keys=True))
        return
    print(f"# Execution brief: {state['task_id']}")
    print(f"\nContract SHA-256: `{state['handoff']['contract_sha256']}`")
    print(f"\nObjective: {state['objective']}")
    print("\n## Success criteria")
    for value in state["success_criteria"]:
        print(f"- {value}")
    print("\n## Evidence and decisions")
    for item in state["evidence"]:
        print(f"- [{item['certainty']}/{item['kind']}] {item['subject']}: {item['finding']}")
    print("\n## Proven starting point")
    print(f"- Paths: {', '.join(state['anchor']['paths'])}")
    print(f"- Rationale: {state['anchor']['rationale']}")
    print(f"- Verification: {state['anchor']['verification']['command']} (exit {state['anchor']['verification']['exit_code']})")
    print("\n## Assigned work")
    for item in items:
        print(f"- {item['id']}: {item['action']}")
        print(f"  - Owner: {item['owner']}")
        print(f"  - Write scope: {', '.join(item['write_scopes'])}")
        print(f"  - Verify: {'; '.join(item['verification'])}")
    print("\nContinue from the proven starting point. Do not redesign the task or modify paths outside your assigned scope. Return a receipt for every item.")


def artifact_records(root: Path, values: list[str]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for value in values:
        candidate, relative = resolve_repo_file(root, value)
        if not candidate.is_file():
            raise RelayError(f"artifact is not a file: {candidate}")
        records.append({"path": relative, "sha256": digest_file(candidate)})
    return records


def cmd_receipt(args: argparse.Namespace) -> None:
    state = load_state(Path(args.state))
    errors, _ = validate_state(state)
    errors.extend(frozen_input_errors(state))
    if errors or not state.get("handoff", {}).get("ready"):
        raise RelayError("receipt requires a valid frozen state")
    item = next((value for value in state["todos"] if value["id"] == args.item_id), None)
    if item is None:
        raise RelayError(f"unknown work item: {args.item_id}")
    root = Path(state["repository"]["root"])
    command_results = [
        run_checked_command(root, command, args.command_timeout) for command in args.command
    ]
    overall_exit = next(
        (result["exit_code"] for result in command_results if result["exit_code"] != 0), 0
    )
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "task_id": state["task_id"],
        "item_id": args.item_id,
        "worker": args.worker,
        "contract_sha256": state["handoff"]["contract_sha256"],
        "summary": args.summary,
        "commands": command_results,
        "exit_code": overall_exit,
        "changes": change_records(root, args.changed_path),
        "artifacts": artifact_records(root, args.artifact),
        "deviations": args.deviation,
        "residual_risks": args.risk,
        "created_at": utc_now(),
    }
    validate_receipt_against_item(receipt, item, root)
    save_json(Path(args.output), receipt, exclusive=True)
    print(args.output)


def cmd_review(args: argparse.Namespace) -> None:
    state = load_state(Path(args.state))
    errors, _ = validate_state(state)
    errors.extend(frozen_input_errors(state))
    if errors or not state.get("handoff", {}).get("ready"):
        raise RelayError("review requires a valid frozen state")
    root = Path(state["repository"]["root"])
    receipts: list[dict[str, Any]] = []
    covered: set[str] = set()
    receipt_workers: set[str] = set()
    receipt_changes: set[str] = set()
    for receipt_path_value in args.receipt:
        receipt_path = Path(receipt_path_value)
        receipt = load_state(receipt_path)
        if receipt.get("contract_sha256") != state["handoff"]["contract_sha256"]:
            raise RelayError(f"receipt uses a different contract: {receipt_path}")
        if receipt.get("exit_code") != 0 and args.verdict == "pass":
            raise RelayError(f"passing review cannot include failed receipt: {receipt_path}")
        item_id = str(receipt.get("item_id", ""))
        item = next((value for value in state["todos"] if value["id"] == item_id), None)
        if item is None:
            raise RelayError(f"receipt references an unknown work item: {item_id!r}")
        if item_id in covered:
            raise RelayError(f"multiple receipts supplied for work item: {item_id}")
        validate_receipt_against_item(receipt, item, root)
        for artifact in receipt.get("artifacts", []):
            candidate, relative = resolve_repo_file(root, str(artifact.get("path", "")))
            if not candidate.is_file() or digest_file(candidate) != artifact["sha256"]:
                raise RelayError(f"artifact hash mismatch: {relative}")
        for change in receipt.get("changes", []):
            candidate, relative = resolve_repo_file(root, str(change.get("path", "")))
            current_hash = digest_file(candidate) if candidate.is_file() else None
            if current_hash != change.get("sha256"):
                raise RelayError(f"changed-path hash mismatch: {relative}")
            receipt_changes.add(relative)
        receipt_workers.add(str(receipt.get("worker", "")))
        covered.add(item_id)
        receipts.append({"path": str(receipt_path), "sha256": digest_file(receipt_path)})
    expected = {item["id"] for item in state["todos"]}
    if args.verdict == "pass" and covered != expected:
        missing = sorted(expected - covered)
        extra = sorted(covered - expected)
        raise RelayError(f"receipt coverage mismatch; missing={missing}, extra={extra}")
    if args.reviewer in receipt_workers:
        raise RelayError("reviewer identity must differ from every receipt worker")

    current_changes = set(repository_changed_paths(root))
    initial_changes = set(state["repository"].get("initial_changed_paths", []))
    anchor_paths = set(state.get("anchor", {}).get("paths", []))
    coordinator_metadata = {
        value
        for value in [
            repo_relative_if_inside(root, Path(state.get("state_file", args.state))),
            repo_relative_if_inside(root, Path(args.output)),
            *(repo_relative_if_inside(root, Path(value)) for value in args.receipt),
        ]
        if value is not None
    }
    worker_scopes = [
        scope for item in state.get("todos", []) for scope in item.get("write_scopes", [])
    ]
    unexpected = sorted(
        path
        for path in current_changes - initial_changes - anchor_paths - coordinator_metadata
        if not any(path_in_scope(path, scope) for scope in worker_scopes)
    )
    if unexpected:
        raise RelayError(f"integrated tree contains changes outside assigned scopes: {unexpected}")
    expected_receipt_changes = {
        path
        for path in current_changes - initial_changes - anchor_paths - coordinator_metadata
        if any(path_in_scope(path, scope) for scope in worker_scopes)
    }
    if args.verdict == "pass" and not expected_receipt_changes.issubset(receipt_changes):
        missing = sorted(expected_receipt_changes - receipt_changes)
        raise RelayError(f"changed paths are missing from worker receipts: {missing}")

    required_commands = {
        command for item in state.get("todos", []) for command in item.get("verification", [])
    }
    supplied_commands = set(args.verify_command)
    missing_review_commands = sorted(required_commands - supplied_commands)
    if missing_review_commands:
        raise RelayError(
            f"review omits required independent verification commands: {missing_review_commands}"
        )
    review_commands = [
        run_checked_command(root, command, args.command_timeout)
        for command in args.verify_command
    ]
    if args.verdict == "pass":
        failed = [result for result in review_commands if result["exit_code"] != 0]
        if failed:
            raise RelayError(
                "passing review has failed independent commands: "
                + ", ".join(result["command"] for result in failed)
            )
    review = {
        "schema_version": SCHEMA_VERSION,
        "task_id": state["task_id"],
        "reviewer": args.reviewer,
        "verdict": args.verdict,
        "contract_sha256": state["handoff"]["contract_sha256"],
        "receipts": receipts,
        "verification": review_commands,
        "notes": args.evidence,
        "created_at": utc_now(),
    }
    save_json(Path(args.output), review, exclusive=True)
    print(args.output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    command = subparsers.add_parser("init", help="create a draft relay state")
    command.add_argument("--state", required=True)
    command.add_argument("--task-id", required=True)
    command.add_argument("--objective", required=True)
    command.add_argument("--success", action="append", required=True)
    command.add_argument("--scope", action="append", default=[])
    command.add_argument("--constraint", action="append", default=[])
    command.add_argument("--coordinator", required=True)
    command.add_argument("--repo", default=".")
    command.set_defaults(func=cmd_init)

    command = subparsers.add_parser("note", help="record repository evidence")
    command.add_argument("--state", required=True)
    command.add_argument("--kind", choices=EVIDENCE_KINDS, required=True)
    command.add_argument("--subject", required=True)
    command.add_argument("--finding", required=True)
    command.add_argument("--evidence", default="")
    command.add_argument("--certainty", choices=CERTAINTY_LEVELS, default="confirmed")
    command.set_defaults(func=cmd_note)

    command = subparsers.add_parser("todo", help="add an owned work item")
    command.add_argument("--state", required=True)
    command.add_argument("--item-id", required=True)
    command.add_argument("--action", required=True)
    command.add_argument("--owner", required=True)
    command.add_argument("--write-scope", action="append", required=True)
    command.add_argument("--verify", action="append", required=True)
    command.add_argument("--depends-on", action="append", default=[])
    command.set_defaults(func=cmd_todo)

    command = subparsers.add_parser("anchor", help="record the first verified code change")
    command.add_argument("--state", required=True)
    command.add_argument("--path", action="append", required=True)
    command.add_argument("--rationale", required=True)
    command.add_argument("--verify-command", required=True)
    command.add_argument("--command-timeout", type=int, default=300)
    command.set_defaults(func=cmd_anchor)

    command = subparsers.add_parser("check", help="validate relay readiness")
    command.add_argument("--state", required=True)
    command.set_defaults(func=cmd_check)

    command = subparsers.add_parser("freeze", help="freeze a validated relay contract")
    command.add_argument("--state", required=True)
    command.set_defaults(func=cmd_freeze)

    command = subparsers.add_parser("brief", help="render a frozen worker brief")
    command.add_argument("--state", required=True)
    command.add_argument("--owner")
    command.add_argument("--format", choices=("markdown", "json"), default="markdown")
    command.set_defaults(func=cmd_brief)

    command = subparsers.add_parser("receipt", help="write a worker receipt")
    command.add_argument("--state", required=True)
    command.add_argument("--item-id", required=True)
    command.add_argument("--worker", required=True)
    command.add_argument("--summary", required=True)
    command.add_argument("--command", action="append", required=True)
    command.add_argument("--command-timeout", type=int, default=300)
    command.add_argument("--changed-path", action="append", required=True)
    command.add_argument("--artifact", action="append", default=[])
    command.add_argument("--deviation", action="append", default=[])
    command.add_argument("--risk", action="append", default=[])
    command.add_argument("--output", required=True)
    command.set_defaults(func=cmd_receipt)

    command = subparsers.add_parser("review", help="write an independent review record")
    command.add_argument("--state", required=True)
    command.add_argument("--reviewer", required=True)
    command.add_argument("--verdict", choices=("pass", "revise"), required=True)
    command.add_argument("--receipt", action="append", required=True)
    command.add_argument("--verify-command", action="append", required=True)
    command.add_argument("--command-timeout", type=int, default=300)
    command.add_argument("--evidence", action="append", required=True)
    command.add_argument("--output", required=True)
    command.set_defaults(func=cmd_review)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except RelayError as exc:
        parser.exit(2, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
