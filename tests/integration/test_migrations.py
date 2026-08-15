from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

REPO_ROOT = Path(__file__).resolve().parents[2]


async def test_schema_applied_via_alembic_upgrade(db_engine: AsyncEngine) -> None:
    script = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))
    (expected_head,) = script.get_heads()

    async with db_engine.connect() as conn:
        result = await conn.execute(text("SELECT version_num FROM alembic_version"))
        (version,) = result.one()
    assert version == expected_head


def test_no_create_all_calls_in_repo() -> None:
    offenders = []
    for path in (REPO_ROOT / "src").rglob("*.py"):
        if "create_all" in path.read_text(encoding="utf-8"):
            offenders.append(path)
    assert not offenders, f"Base.metadata.create_all found in: {offenders}"
