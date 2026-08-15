#!/usr/bin/env python3
"""Enforce Conventional Commits (https://www.conventionalcommits.org) on the
commit message. Kept as a tiny local script rather than pulling in commitlint's
Node toolchain for one regex check.
"""

import re
import sys

TYPES = "feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert"
PATTERN = re.compile(rf"^({TYPES})(\([\w./-]+\))?!?: .{{1,88}}$")


def main() -> int:
    msg_path = sys.argv[1]
    with open(msg_path, encoding="utf-8") as f:
        first_line = f.readline().rstrip("\n")

    if first_line.startswith("Merge ") or first_line.startswith("fixup!"):
        return 0

    if not PATTERN.match(first_line):
        print(
            "Commit message does not follow Conventional Commits:\n"
            f'  "{first_line}"\n\n'
            f"Expected: <{TYPES}>(optional-scope): summary\n"
            "Example:  feat(api): add idempotency-key support to event ingest",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
