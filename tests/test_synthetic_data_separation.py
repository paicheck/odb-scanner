"""The simulator and the seeder must not be able to reach the car's record.

Synthetic readings are indistinguishable from measured ones once they are in
the same file under the same VIN -- that is how 265,014 simulator readings and a
month of seeded example history became part of this vehicle's history. The
defences tested here are the ones about *where the data lands*:

    * the simulator answers with its own VIN, not the car's
    * the collector is pointed at its own database file (launcher-enforced)
    * config loading refuses a configuration where the two files are the same
    * the seeder writes example history under its own VIN

The complementary defence -- labelling rows the collector itself writes while
talking to the simulator -- is tests/test_simulated_provenance.py.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# A synthetic stand-in for the car, deliberately not the simulator's demo
# VIN: these tests prove the two identities never mix. Never the real one.
CAR_VIN = "WVWZZZE1ZMS000002"
DEMO_VIN = "WVWZZZE1ZMS000001"


# --- the simulator's identity ------------------------------------------------
def test_the_simulators_default_vin_is_not_the_cars():
    """A simulator that reports the car's VIN is the car, as far as the
    database can tell. This is the whole failure mode in one line.

    Reads the module's import-time default, so a change to the constant is
    caught even though tests elsewhere pin the car's VIN for their own run.
    """
    from simulator.vehicle import DEMO_VIN
    from simulator.vehicle import VIN
    assert VIN == DEMO_VIN, "the simulator's default identity changed"
    assert DEMO_VIN.decode() != CAR_VIN
    # VIN-shaped: the transport, the parser and the year decoder all assume 17.
    assert len(DEMO_VIN) == 17


def test_a_pinned_vin_does_not_leak_into_the_next_server():
    """The VIN is process-global, so it must be scoped to the server using it.

    Without this, one test (or one run) that pins the car's VIN silently
    changes the identity every later simulator run reports.
    """
    from simulator import vehicle

    before = vehicle.VIN
    server = vehicle.SimServer("127.0.0.1", 0, vin=CAR_VIN)  # never started
    try:
        assert vehicle.VIN == CAR_VIN.encode()
    finally:
        server.stop()
    assert vehicle.VIN == before


def test_set_vin_accepts_a_string_or_bytes():
    """main.py passes the configured VIN, which comes from YAML as a str."""
    from simulator import vehicle

    original = vehicle.VIN
    try:
        vehicle.set_vin(CAR_VIN)
        assert vehicle.VIN == CAR_VIN.encode()
        vehicle.set_vin(b"OTHER")
        assert vehicle.VIN == b"OTHER"
        # None means "leave it alone": SimServer(vin=None) is the default path,
        # and `main.py simulate` passes None when simulation.vin is unset.
        vehicle.set_vin(None)
        assert vehicle.VIN == b"OTHER"
    finally:
        vehicle.set_vin(original)


def test_the_simulator_config_has_its_own_identity_and_file():
    from config import load_config
    cfg = load_config(Path(__file__).resolve().parents[1] / "config.yaml",
                      local_path=os.devnull)
    assert cfg.get("simulation.vin") != cfg.get("vehicle.vin")
    assert Path(cfg.simulator_db_path) != Path(cfg.db_path)
    assert Path(cfg.seed_db_path) != Path(cfg.db_path)


# --- config loading ----------------------------------------------------------
def test_config_refuses_a_simulator_database_that_is_the_cars_database(tmp_path):
    """The mistake this whole change exists to prevent, caught at startup.

    It cost nothing to make when it was made -- one line in a local override --
    and cost a 544,237-row clean-up to undo.
    """
    from config import ConfigError, load_config

    (tmp_path / "config.yaml").write_text(
        "database:\n  path: data/car.db\n"
        "simulation:\n  db_path: data/car.db\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_config(tmp_path / "config.yaml")
    assert "simulation.db_path" in str(exc.value)
    assert "same file" in str(exc.value)


def test_config_refuses_a_seed_database_that_is_the_cars_database(tmp_path):
    from config import ConfigError, load_config

    (tmp_path / "config.yaml").write_text(
        "database:\n  path: data/car.db\n"
        "seed:\n  db_path: data/car.db\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_config(tmp_path / "config.yaml")
    assert "seed.db_path" in str(exc.value)


def test_separate_files_load_without_complaint(tmp_path):
    from config import load_config

    (tmp_path / "config.yaml").write_text(
        "database:\n  path: data/car.db\n"
        "simulation:\n  db_path: data/sim.db\n"
        "seed:\n  db_path: data/seed.db\n", encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.db_path == "data/car.db"
    assert cfg.simulator_db_path == "data/sim.db"


def test_odb_db_path_overrides_the_configured_database(monkeypatch, tmp_path):
    """How tools/start_scanner.py redirects one run without editing config."""
    from config import load_config

    (tmp_path / "config.yaml").write_text(
        "database:\n  path: data/car.db\n", encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.db_path == "data/car.db"
    monkeypatch.setenv("ODB_DB_PATH", "data/sim.db")
    assert cfg.db_path == "data/sim.db"


# --- the seeder --------------------------------------------------------------
def test_the_seeder_writes_under_its_own_vin(tmp_path):
    """`main.py seed` is the second synthetic source found in the real file."""
    from tools.seed_demo_data import DEMO_VIN as seeder_vin
    from tools.seed_demo_data import seed

    assert seeder_vin == DEMO_VIN
    db = str(tmp_path / "seed.db")
    seed(db, days=1)
    from database.repository import Repository
    with Repository(db) as repo:
        vins = [row["vin"] for row in repo.conn.execute("SELECT vin FROM vehicles")]
        assert vins == [DEMO_VIN]
        assert CAR_VIN not in vins


def test_the_seeder_warns_when_the_file_also_holds_real_data(tmp_path, capsys):
    """It cannot stop a user pointing it at the car's file, so it says so --
    and still keeps the two sets on separate vehicle rows."""
    from database.repository import Repository
    from tools.seed_demo_data import DEMO_VIN, seed

    db = str(tmp_path / "mixed.db")
    with Repository(db) as repo:
        repo.ensure_vehicle(CAR_VIN)
    seed(db, days=1)
    out = capsys.readouterr().out
    assert CAR_VIN in out and DEMO_VIN in out
    with Repository(db) as repo:
        vins = sorted(row["vin"] for row in repo.conn.execute(
            "SELECT vin FROM vehicles"))
        assert vins == sorted([CAR_VIN, DEMO_VIN])


# --- main.py -----------------------------------------------------------------
def test_seed_command_refuses_the_cars_database(tmp_path, monkeypatch, capsys):
    """The command-level guard, for a config that predates the validation."""
    import yaml

    import main as cli
    from config import Config, DEFAULT_PATH

    with open(DEFAULT_PATH, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    data.setdefault("database", {})["path"] = str(tmp_path / "car.db")
    # Bypass load_config's own check: this is the "already-running" case, where
    # the file on disk was written before the check existed.
    data.setdefault("seed", {})["db_path"] = str(tmp_path / "car.db")
    monkeypatch.chdir(tmp_path)
    assert cli.cmd_seed(Config(data)) == 2
    assert "refusing" in capsys.readouterr().err
    assert not (tmp_path / "car.db").exists(), "seeded the car's database anyway"


def test_absolute_and_relative_paths_to_the_same_file_are_caught(tmp_path,
                                                                monkeypatch):
    # The car's database configured absolutely and the simulator's relatively:
    # different strings, same file. Comparing strings let it through.
    from config import ConfigError, load_config
    monkeypatch.chdir(tmp_path)
    car = (tmp_path / "data" / "car.db").resolve()
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        f"database:\n  path: '{car.as_posix()}'\n"
        "simulation:\n  db_path: data/car.db\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(cfg_file, local_path=tmp_path / "none.local.yaml")
