"""Temporary debug: probe ECU discovery against the simulator."""
import logging
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.basicConfig(level=logging.WARNING)

from simulator.vehicle import SimServer
from diagnostic.elm327 import Elm327Transport
from diagnostic.connection import DiagnosticConnection
from diagnostic import uds
from diagnostic.ecus import get_ecu

srv = SimServer("127.0.0.1", 35199)
srv.start()
try:
    c = DiagnosticConnection(
        Elm327Transport(host="127.0.0.1", tcp_port=35199, timeout=3.0))
    c.open()
    print("VIN:", c.read_vin())

    # Now the REAL collector path
    import tempfile
    from config import load_config
    from database.repository import Repository
    from collector import Collector

    with tempfile.TemporaryDirectory() as tmp:
        repo = Repository(f"{tmp}/dbg.db")
        col = Collector(load_config(), repo)
        col.conn.t.host, col.conn.t.tcp_port = "127.0.0.1", 35199
        col.conn.t._dev = None
        col.conn.t._opened = False
        col.conn.open()
        col.vehicle_id = repo.ensure_vehicle("WVWZZZE1ZMP087053")
        col.vin = "WVWZZZE1ZMP087053"
        print("discover_ecus:", col.discover_ecus())
        rows = repo.conn.execute("SELECT key, status FROM ecus").fetchall()
        print("db:", [(r["key"], r["status"]) for r in rows])
        tx = repo.conn.execute(
            "SELECT direction, ecu, payload, purpose FROM tx_log "
            "ORDER BY id DESC LIMIT 8").fetchall()
        for r in tx:
            print("tx:", dict(r))
        col.close()
finally:
    srv.stop()
