"""Verify the clean source package against SHA256SUMS.txt; never change files.

Run from any working directory: python path/to/tools/verify_source.py
Extra files created locally (settings, logs, caches, builds) are not evaluated.
After intentionally editing a source file, a mismatch is expected.
"""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import re
import sys


def verify_manifest(root: Path) -> tuple[int, list[str]]:
    """Return (checked entries, errors) with no network or filesystem writes."""
    root = Path(root).resolve()
    manifest = root / 'SHA256SUMS.txt'
    try:
        lines = manifest.read_text(encoding='utf-8-sig').splitlines()
    except (OSError, UnicodeError) as exc:
        return 0, [f'Cannot read {manifest.name}: {exc}']
    checked = 0
    errors: list[str] = []
    seen: set[str] = set()
    for number, line in enumerate(lines, 1):
        if not line:
            continue
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        if not match:
            errors.append(f'Malformed manifest line {number}')
            continue
        expected, name = match.groups()
        path = PurePosixPath(name)
        if (path.is_absolute() or not path.parts or '..' in path.parts
                or '\\' in name or ':' in name or path.as_posix() != name):
            errors.append(f'Unsafe path on manifest line {number}')
            continue
        if name in seen:
            errors.append(f'Duplicate manifest entry: {name}')
            continue
        seen.add(name)
        target = root.joinpath(*path.parts)
        if not target.resolve().is_relative_to(root):
            errors.append(f'Path escapes package root: {name}')
            continue
        checked += 1
        try:
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError as exc:
            errors.append(f'Cannot read {name}: {exc}')
            continue
        if actual != expected:
            errors.append(f'SHA-256 mismatch: {name}')
    if checked == 0:
        errors.append('Manifest contains no verifiable files')
    return checked, errors


def main() -> int:
    checked, errors = verify_manifest(Path(__file__).resolve().parents[1])
    if errors:
        print(f'FAILED: {len(errors)} problem(s), {checked} manifest entries checked.')
        for error in errors:
            print(error)
        return 1
    print(f'OK: {checked} source files match SHA256SUMS.txt.')
    print('This verifies bytes only, not Windows runtime behavior. Local extra files are ignored.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
