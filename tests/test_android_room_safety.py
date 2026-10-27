"""The Android Room database never wipes itself on a version change.

fallbackToDestructiveMigration() dropped every table -- un-uploaded readings
and the tx_log safety manifest included -- on any version step without a
migration, and on a downgrade. exportSchema=false meant no schema history to
test a migration against. Room's builder needs an Android runtime, so this
is a static check of the source and the committed schema.
"""
import json
import re
from pathlib import Path

ANDROID = Path(__file__).resolve().parent.parent / "android" / "app"
DB_SRC = (ANDROID / "src/main/kotlin/com/odbscanner/db/AppDatabase.kt").read_text(
    encoding="utf-8")


def test_no_destructive_fallback():
    code = "\n".join(line for line in DB_SRC.splitlines()
                     if not line.lstrip().startswith(("*", "//", "/*")))
    assert "fallbackToDestructiveMigration" not in code


def test_schema_is_exported_and_committed_for_the_current_version():
    assert re.search(r"exportSchema\s*=\s*true", DB_SRC)
    version = int(re.search(r"version\s*=\s*(\d+)", DB_SRC).group(1))
    schema = ANDROID / "schemas" / "com.odbscanner.db.AppDatabase" / f"{version}.json"
    assert schema.is_file(), f"commit the exported schema {schema.name}"
    exported = json.loads(schema.read_text(encoding="utf-8"))
    assert exported["database"]["version"] == version


def test_every_version_step_has_a_migration():
    version = int(re.search(r"version\s*=\s*(\d+)", DB_SRC).group(1))
    for v in range(1, version):
        assert f"MIGRATION_{v}_{v + 1}" in DB_SRC
