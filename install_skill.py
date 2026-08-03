#!/usr/bin/env python3
"""Install the Prework_Codex skill into a Codex home or directory."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path


SKILL_NAME = "prepare-execution-handoff"


class InstallError(RuntimeError):
    """Raised when an installation would be unsafe or cannot be completed."""


def _resolved(path: Path) -> Path:
    """Return an absolute path while allowing the final component to be absent."""

    try:
        return path.expanduser().resolve()
    except OSError as exc:
        raise InstallError(f"cannot resolve path {path}: {exc}") from exc


def _nested(path: Path, parent: Path) -> bool:
    """Return whether path is parent or is below parent."""

    return path == parent or parent in path.parents


def _existing_symlink(root: Path) -> Path | None:
    """Find a destination symlink without following directory links."""

    for current, directories, files in os.walk(root, followlinks=False):
        for name in (*directories, *files):
            candidate = Path(current) / name
            if candidate.is_symlink():
                return candidate
    return None


def _symlink_component(path: Path) -> Path | None:
    """Return the first symlink in an absolute path, including its parents."""

    absolute = Path(os.path.abspath(path.expanduser()))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            metadata = current.lstat()
            if current.parent == Path(absolute.anchor) and getattr(metadata, "st_uid", -1) == 0:
                continue
            return current
        if not current.exists():
            break
    return None


def install_skill(destination: Path, *, force: bool = False) -> Path:
    """Copy the bundled skill to destination and return its resolved path."""

    source = _resolved(Path(__file__).parent / "skills" / SKILL_NAME)
    raw_target = Path(destination).expanduser()
    try:
        raw_target = Path(os.path.abspath(raw_target))
    except OSError as exc:
        raise InstallError(f"cannot resolve path {destination}: {exc}") from exc
    destination_link = _symlink_component(raw_target)
    if destination_link is not None:
        raise InstallError(f"refusing to install through destination symlink: {destination_link}")
    target = _resolved(raw_target)

    if not source.is_dir():
        raise InstallError(f"bundled skill directory does not exist: {source}")
    if _nested(target, source) or _nested(source, target):
        raise InstallError("destination must be separate from the bundled skill directory")
    source_link = _existing_symlink(source)
    if source_link is not None:
        raise InstallError(f"refusing to install a skill containing a symlink: {source_link}")
    if target.exists():
        if not target.is_dir():
            raise InstallError(f"destination exists and is not a directory: {target}")
        if not force:
            raise InstallError(
                f"destination already exists: {target}; use --force to allow overwrite"
            )
        link = _existing_symlink(target)
        if link is not None:
            raise InstallError(f"refusing to overwrite through nested destination symlink: {link}")
    else:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise InstallError(f"cannot create destination parent {target.parent}: {exc}") from exc

    staging_root = Path(tempfile.mkdtemp(prefix=f".{target.name}.install-", dir=target.parent))
    staged = staging_root / "next"
    previous = staging_root / "previous"
    try:
        shutil.copytree(source, staged, symlinks=False)
        if target.exists():
            target.rename(previous)
        try:
            staged.rename(target)
        except OSError:
            if previous.exists() and not target.exists():
                previous.rename(target)
            raise
        if previous.exists():
            shutil.rmtree(previous)
    except OSError as exc:
        raise InstallError(f"installation failed for {target}: {exc}") from exc
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
    return target


def _destination_from_args(args: argparse.Namespace) -> Path:
    if args.destination is not None:
        return args.destination

    home_value = args.codex_home
    if home_value is None:
        home_value = os.environ.get("CODEX_HOME")
    home = Path(home_value).expanduser() if home_value else Path.home() / ".codex"
    return home / "skills" / SKILL_NAME


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy the bundled Prework_Codex skill to a chosen location."
    )
    location = parser.add_mutually_exclusive_group()
    location.add_argument(
        "--codex-home",
        metavar="PATH",
        help="Codex home; installs below PATH/skills/" + SKILL_NAME,
    )
    location.add_argument(
        "--destination",
        metavar="PATH",
        help="exact destination directory for the skill",
    )
    parser.add_argument(
        "--force",
        "--overwrite",
        dest="force",
        action="store_true",
        help="replace an existing destination with a complete staged copy",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        target = install_skill(_destination_from_args(args), force=args.force)
    except (InstallError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Installed {SKILL_NAME} to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
