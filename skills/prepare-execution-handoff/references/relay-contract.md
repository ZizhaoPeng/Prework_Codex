# Prework_Codex Continuation Contract

## Contents

1. State lifecycle
2. Central state fields
3. Worker receipt fields
4. Review record fields
5. Integrity and ownership rules
6. Failure handling

## 1. State lifecycle

The central state moves through two phases:

- `draft`: the lead may add evidence, queue items, and an anchor.
- `frozen`: workers may read the state but must not mutate it.

The `freeze` command computes `handoff.contract_sha256` over the execution-relevant fields. Receipts and reviews repeat this digest so the reviewer can detect stale or substituted context.

## 2. Central state fields

| Field | Purpose |
|---|---|
| `schema_version` | Contract compatibility. |
| `task_id` | Stable task identifier. |
| `objective` | Desired outcome, not an implementation slogan. |
| `success_criteria` | Observable completion gates. |
| `constraints` | Boundaries that workers must preserve. |
| `repository` | Root, branch, commit, and initial working-tree digest. |
| `coordinator` | Lead role that owns decisions and final acceptance. |
| `evidence` | Files, commands, decisions, rejected routes, and risks with certainty labels. |
| `todos` | Bounded work items with owners, scopes, dependencies, and verification commands. |
| `anchor` | First real change, captured command result, and file/diff hashes. |
| `handoff` | Readiness, freeze time, and contract digest. |
| `events` | Append-only mutation trail for the draft phase. |

Evidence kinds are `file`, `command`, `decision`, `rejected`, and `risk`. Certainty is `confirmed` or `provisional`. Rejected routes must carry a concrete reason; do not record vague preference as evidence.

## 3. Worker receipt fields

A receipt is a separate JSON file and never changes the frozen central state. It contains:

- schema version and task/work-item identifiers;
- worker identity;
- frozen contract SHA-256;
- summary and deviations;
- commands executed by the receipt command and their captured results;
- changed paths with content/diff hashes;
- artifact paths and SHA-256 values;
- creation time.

Use one append-only receipt per completed work item. The CLI rejects an existing output path. If an item is retried by another worker, create a new receipt and a new writable directory or branch; never overwrite the first worker's evidence.

## 4. Review record fields

Only the lead or designated reviewer writes the review record. A passing review requires one valid receipt for every queue item. The record contains:

- reviewer and verdict (`pass` or `revise`);
- frozen contract SHA-256;
- receipt paths and hashes;
- independently executed verification commands and captured results;
- reviewer notes;
- creation time.

The `review` command requires a reviewer identity different from every worker, executes the required verification commands itself, and verifies receipt coverage, contract digests, changed-path ownership, current artifact hashes, and integrated-tree scope before allowing `pass`.

## 5. Integrity and ownership rules

- Treat the repository baseline and frozen state as shared read-only inputs.
- Give concurrent owners non-overlapping writable paths.
- Keep final decision files, integration reports, and route updates under the coordinator's ownership.
- Hash inputs and outputs with SHA-256; do not substitute timestamps for content identity.
- Preserve raw command output or a stable path to it when a summary would hide failure details.
- Re-run acceptance checks from the integrated tree rather than accepting isolated worker checks alone.

## 6. Failure handling

Return to the lead when a readiness check fails. Do not weaken the schema to make a handoff pass.

When execution fails:

1. Preserve the worker directory and receipt.
2. Record the failure and its evidence.
3. Decide whether the original contract remains valid.
4. If inputs or ownership change, create a new state version and digest.
5. Assign retries to new scopes or directories with explicit lineage.
