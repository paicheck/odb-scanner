"""Shared test fixtures and import bootstrap for the odb-scanner test suite.

Living here rather than in the test module means a test file can import
project packages at the top, in the normal order, without each one having to
repeat the sys.path dance.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

# The project is a flat set of top-level packages (diagnostic, analysis,
# database, ...) run as scripts from the repo root, not an installed
# distribution, so the repo root has to be importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Starlette's TestClient sends `Host: testserver`. The dashboard refuses
# unknown Host names (DNS-rebinding guard, web/auth.py), so the suite
# declares it the same way an operator would declare a proxy's name.
os.environ.setdefault("ODB_ALLOWED_HOSTS", "testserver")


@pytest.fixture()
def repo():
    """A throwaway SQLite database in a temp dir, closed before cleanup.

    close() must run before TemporaryDirectory cleanup or Windows refuses to
    delete the still-open WAL files.
    """
    from database.repository import Repository
    with tempfile.TemporaryDirectory() as tmp:
        r = Repository(os.path.join(tmp, "test.db"))
        yield r
        r.close()  # release WAL locks before TemporaryDirectory cleanup


@pytest.fixture()
def cfg():
    """Default config. Callers override the DB path when they need isolation."""
    from config import load_config
    return load_config()


@pytest.fixture(autouse=True)
def _restore_experiment_arms():
    """Keep the shared experiment-matrix state out of other tests.

    ``tools.experiment_record.ARMS`` is module-global, and
    tools/diagnose_meb_path.py writes each arm's result back into it. Without
    this, running the matrix (tests/test_meb_path.py) leaves every arm marked
    "ran", and a later test that asserts the arms are unrun fails purely
    because of test order -- which is how a genuine failure can be mistaken for
    a fixture problem, or hidden entirely. Snapshot and restore around every
    test so the suite is order-independent (CI also runs it in random order).
    """
    try:
        from tools.experiment_record import ARMS
    except Exception:  # never let the guard break an unrelated test
        yield
        return
    snapshot = [(arm, arm.actual, arm.interpret) for arm in ARMS]
    try:
        yield
    finally:
        for arm, actual, interpret in snapshot:
            arm.actual = actual
            arm.interpret = interpret
