import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "generate_openapi_spec.py"
SPEC_PATH = REPO_ROOT / "docs" / "openapi.json"


def test_committed_spec_matches_the_code() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True, cwd=REPO_ROOT
    )
    assert result.returncode == 0, result.stderr


def test_check_mode_fails_on_drift(tmp_path: Path) -> None:
    original = SPEC_PATH.read_text(encoding="utf-8")
    try:
        SPEC_PATH.write_text(json.dumps({"drifted": True}), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--check"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert result.returncode == 1
        assert "out of date" in result.stderr
    finally:
        SPEC_PATH.write_text(original, encoding="utf-8")
