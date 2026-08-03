---
name: prepare-execution-handoff
description: Prepare evidence-backed coding handoffs that another Codex agent can continue without reconstructing the repository context. Use for large or execution-heavy tasks that will move from a lead agent to one or more implementation workers, especially when the lead must inspect the code, eliminate alternatives, land and verify a first real change, freeze write ownership, generate worker briefs, collect receipts, and independently review the result.
---

# Prework_Codex

Turn a partially understood coding request into a verified continuation point. Preserve the working state that matters for execution instead of handing over a standalone plan.

## Core rule

Do not delegate after analysis alone. Before assigning implementation work, make one meaningful, reversible code change and validate it. Hand workers a frozen evidence trail plus that proven starting point.

Keep architecture choices, ambiguous requirements, security decisions, cross-worker conflict resolution, and final acceptance with the lead agent. Delegate only bounded execution.

## Workflow

1. Inspect the repository and establish the baseline.
   - Read the relevant files, call paths, tests, configuration, and repository state.
   - Record concrete observations, command results, remaining uncertainty, and rejected routes.
   - Distinguish verified facts from provisional judgments.

2. Build an active work queue.
   - Define success criteria before editing.
   - Split work into items with explicit write scopes, dependencies, verification commands, and owners.
   - Prevent concurrent workers from sharing writable paths.

3. Land the anchor change.
   - Make the smallest change that tests the intended direction against the real codebase.
   - Let the relay CLI run a relevant test, build, typecheck, lint, or smoke command; never supply a claimed exit status.
   - Record changed paths, rationale, captured command output, actual exit status, file hashes, and diff hash.
   - If the change invalidates the approach, revise the queue before delegation.

4. Freeze the continuation contract.
   - Run `relay_state.py freeze` only after evidence, queue, and anchor checks pass.
   - Treat the frozen state as read-only input for workers.
   - Generate a worker brief from the frozen state rather than rewriting the context by hand.

5. Execute through isolated workers.
   - Assign each worker exactly one or more queue items and an exclusive write scope.
   - Require workers to continue from the anchor rather than reopen the design question.
   - Require a machine-readable, append-only receipt with executed commands, changed paths, artifacts, hashes, deviations, and residual risks.

6. Review independently.
   - Do not trust a worker's success statement by itself.
   - Use a reviewer identity different from every worker and let the review command re-run the smallest commands that prove the acceptance criteria.
   - Check the frozen contract hash, receipt coverage, artifact hashes, scope compliance, integration behavior, and unresolved risks.
   - Write the final verdict only from the lead/reviewer role. Return failed items for correction and review again.

## State tool

Use `scripts/relay_state.py` with Python 3 and no third-party packages.

```bash
python3 scripts/relay_state.py init \
  --state .codex/execution-relay/example.json \
  --task-id example \
  --objective "Implement the requested behavior" \
  --success "Targeted tests pass" \
  --scope src --coordinator lead

python3 scripts/relay_state.py note \
  --state .codex/execution-relay/example.json \
  --kind file --subject src/module.py \
  --finding "This is the active implementation path"

python3 scripts/relay_state.py todo \
  --state .codex/execution-relay/example.json \
  --item-id impl --action "Complete the bounded implementation" \
  --owner worker-1 --write-scope src/module.py \
  --verify "pytest tests/test_module.py"

python3 scripts/relay_state.py anchor \
  --state .codex/execution-relay/example.json \
  --path src/module.py --rationale "Proves the selected extension point" \
  --verify-command "pytest tests/test_module.py -q"

python3 scripts/relay_state.py freeze --state .codex/execution-relay/example.json
python3 scripts/relay_state.py brief --state .codex/execution-relay/example.json --owner worker-1

python3 scripts/relay_state.py receipt \
  --state .codex/execution-relay/example.json \
  --item-id impl --worker worker-1 --summary "Completed implementation" \
  --command "pytest tests/test_module.py -q" \
  --changed-path src/module.py --artifact src/module.py \
  --output .codex/execution-relay/impl-receipt.json

python3 scripts/relay_state.py review \
  --state .codex/execution-relay/example.json \
  --reviewer lead-reviewer --verdict pass \
  --receipt .codex/execution-relay/impl-receipt.json \
  --verify-command "pytest tests/test_module.py -q" \
  --evidence "Integrated behavior independently verified" \
  --output .codex/execution-relay/review.json
```

Use `receipt` after worker execution and `review` after independent verification. Run `check` at any time for a machine-readable readiness report.

## Contract details

Read [references/relay-contract.md](references/relay-contract.md) when implementing integrations, generating receipts, reviewing multiple workers, or diagnosing a failed readiness check.

## Stop conditions

Stop delegation and return to the lead when:

- an executed anchor or review command fails;
- evidence conflicts with the proposed direction;
- writable scopes overlap;
- a worker needs to change frozen shared inputs;
- the contract hash or artifact hashes do not match;
- a receipt path escapes its assigned write scope or attempts to overwrite earlier evidence;
- the reviewer identity matches a worker identity;
- a task requires a new architecture, security, product, or release decision.
