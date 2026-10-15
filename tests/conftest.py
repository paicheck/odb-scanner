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
