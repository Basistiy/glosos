#!/usr/bin/env python3
"""Restore project files from backup files after a bad agent run.

Default behavior:
- scans for backup files
- prints planned restore actions (dry run)

Use --apply to execute restore operations.
Use --cleanup-backups to remove backup files after successful restore.
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
from pathlib import Path


BACKUP_SUFFIXES = (".bak", ".backup", ".orig", "~")
SKIP_DIRS = {".git", ".venv", "__pycache__"}


def is_backup_file(path: Path) -> bool:
    return any(path.name.endswith(suffix) for suffix in BACKUP_SUFFIXES)


def backup_to_target(backup_path: Path) -> Path:
    name = backup_path.name
    for suffix in BACKUP_SUFFIXES:
        if name.endswith(suffix):
            return backup_path.with_name(name[: -len(suffix)])
    return backup_path


def should_scan(part: str) -> bool:
    return part not in SKIP_DIRS


def discover_backups(root: Path) -> list[Path]:
    backups: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if not all(should_scan(part) for part in path.parts):
            continue
        if is_backup_file(path):
            backups.append(path)
    return sorted(backups)


def format_rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply restore operations.")
    parser.add_argument(
        "--cleanup-backups",
        action="store_true",
        help="Delete backup files after successful restore.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root to scan (default: current directory).",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    backups = discover_backups(root)

    if not backups:
        print("No backup files found.")
        return 0

    print(f"Found {len(backups)} backup file(s) under {root}")

    restored = 0
    skipped = 0
    deleted_backups = 0

    for backup in backups:
        target = backup_to_target(backup)
        rel_backup = format_rel(backup, root)
        rel_target = format_rel(target, root)

        if not target.exists():
            action = f"restore missing target: {rel_target} <- {rel_backup}"
        else:
            same = filecmp.cmp(backup, target, shallow=False)
            if same:
                skipped += 1
                print(f"skip (identical): {rel_target}")
                if args.apply and args.cleanup_backups:
                    backup.unlink()
                    deleted_backups += 1
                continue
            action = f"overwrite changed target: {rel_target} <- {rel_backup}"

        if not args.apply:
            print(f"[dry-run] {action}")
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, target)
        restored += 1
        print(f"[applied] {action}")

        if args.cleanup_backups:
            backup.unlink()
            deleted_backups += 1

    mode = "applied" if args.apply else "dry-run"
    print(
        f"Done ({mode}). restored={restored}, skipped={skipped}, "
        f"backups_deleted={deleted_backups}"
    )
    if not args.apply:
        print("Re-run with --apply to execute.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
