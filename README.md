# Prework_Codex

Prework_Codex is a model-agnostic Codex skill for turning a partially explored coding task into a verified continuation point. Before implementation moves to another agent, the lead inspects the real repository, records evidence and rejected routes, lands one meaningful change, validates it, and freezes a bounded work contract. The next agent continues from that proven state instead of reconstructing the project from a standalone plan.

The project keeps execution context, write ownership, command evidence, artifact hashes, and acceptance checks explicit. It also separates worker execution from final review, making multi-agent handoffs easier to audit and less likely to duplicate repository discovery.

## Install

The standard-library-only installer copies the stable `$prepare-execution-handoff` skill into a Codex home. The default home is `$CODEX_HOME` when set, otherwise `~/.codex`:

```bash
python3 install_skill.py --codex-home ~/.codex
```

To choose the exact skill directory, use `--destination`:

```bash
python3 install_skill.py --destination /tmp/codex/skills/prepare-execution-handoff
```

An existing destination is left untouched unless `--force` is supplied. Forced updates stage a complete copy beside the destination and replace the old skill directory, so removed package files do not linger. The installer refuses source/destination nesting and symlinked package content. It accepts `--help` for all options and uses no third-party Python packages.

## Workflow

Use the skill to establish a repository baseline, define bounded work items, execute and capture validation for an anchor change, freeze the continuation contract, and collect append-only worker receipts for independent review. The included state tool executes verification commands and checks contract identity, changed-path ownership, repository containment, artifact hashes, receipt coverage, reviewer separation, and integrated-tree scope.

Invoke it in Codex with a request such as:

```text
Use $prepare-execution-handoff to establish a verified continuation point before delegating this implementation.
```

## Validation

The repository test suite exercises the full relay lifecycle and adversarial gates for forged command results, changed anchors, path traversal, out-of-scope writes, artifact tampering, self-review, evidence overwrite, incomplete receipts, stale shared inputs, unsafe replacement, and symlinked installation paths.

```bash
python3 -m unittest discover -s tests -v
```
