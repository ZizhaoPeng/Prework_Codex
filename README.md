# Execution Relay for Codex

Execution Relay for Codex is a model-agnostic skill for turning a partially explored coding task into a verified handoff. It keeps the working context, evidence, ownership boundaries, and acceptance checks explicit so another agent can continue without repeating repository discovery.

## Install

The standard-library-only installer copies `skills/prepare-execution-handoff` into a Codex home. The default home is `$CODEX_HOME` when set, otherwise `~/.codex`:

```bash
python3 install_skill.py --codex-home ~/.codex
```

To choose the exact skill directory, use `--destination`:

```bash
python3 install_skill.py --destination /tmp/codex/skills/prepare-execution-handoff
```

An existing destination is left untouched unless `--force` is supplied. Forced updates stage a complete copy beside the destination and replace the old skill directory, so removed package files do not linger. The installer refuses source/destination nesting and symlinked package content. It accepts `--help` for all options and uses no third-party Python packages.

## Workflow

Use the skill to establish a repository baseline, define bounded work items, execute and capture validation for an anchor change, freeze the continuation contract, and collect append-only worker receipts for independent review. The included relay script executes verification commands and checks contract identity, changed-path ownership, repository containment, artifact hashes, receipt coverage, reviewer separation, and integrated-tree scope.

Invoke it in Codex with a request such as:

```text
Use $prepare-execution-handoff to establish a verified continuation point before delegating this implementation.
```

## Validation

The repository test suite exercises the full relay lifecycle and adversarial gates for forged command results, changed anchors, path traversal, out-of-scope writes, artifact tampering, self-review, evidence overwrite, incomplete receipts, stale shared inputs, unsafe replacement, and symlinked installation paths.

```bash
python3 -m unittest discover -s tests -v
```
