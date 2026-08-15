#!/usr/bin/env python3
"""Regenerates docs/openapi.json from the running app's route/schema definitions.

CI runs this into a temp file and diffs it against the committed copy, failing the
build if they differ — the committed spec is documentation, and undocumented drift
between it and the code is exactly the failure mode that check exists to catch.
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = REPO_ROOT / "docs" / "openapi.json"


def generate() -> dict[str, object]:
    from relay.api.app import create_app

    return create_app().openapi()


def main() -> int:
    spec = generate()
    rendered = json.dumps(spec, indent=2, sort_keys=True) + "\n"

    if "--check" in sys.argv:
        current = SPEC_PATH.read_text(encoding="utf-8") if SPEC_PATH.exists() else ""
        if current != rendered:
            print(
                f"{SPEC_PATH.relative_to(REPO_ROOT)} is out of date with the code.\n"
                "Run `make openapi` and commit the result.",
                file=sys.stderr,
            )
            return 1
        print(f"{SPEC_PATH.relative_to(REPO_ROOT)} matches the code.")
        return 0

    SPEC_PATH.write_text(rendered, encoding="utf-8")
    print(f"wrote {SPEC_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
