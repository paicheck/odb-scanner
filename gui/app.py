"""Small desktop GUI for the odb_scanner.

Uses Tkinter (no extra install needed on Windows/macOS/Linux with Python 3).
Shows the same provenance information as the web dashboard but in a
standalone window.  All data is read-only; nothing is sent to the adapter.

Features
--------
- Vehicle identification (VIN from the database)
- Battery overview: live / imported / stale / unavailable counts
- Per-measurement state table (same labels as the web /sources page)
- Buttons to launch the built-in simulator (harness self-test) or open
  the web dashboard in the default browser
- No adapter required; everything is read from the local database

The GUI is deliberately narrow in scope: it does not attempt to replace
the web dashboard or the CLI.  It is a convenience wrapper for the
most-commonly-needed read-only queries.
"""
from __future__ import annotations

import sqlite3
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers: read the database that the collector/importer already populates
# ---------------------------------------------------------------------------

DB_PATH = Path(r"C:\odb_scanner\data\real_car.db")
PROVENANCE_PREFIX = "imported:"  # matches analysis/provenance.py IMPORTED_PREFIX


def _conn():
    return sqlite3.connect(str(DB_PATH))


def _exe(sql: str, params: tuple | None = None) -> list[dict]:
    c = _conn()
    try:
        cur = c.execute(sql, params or ())
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        return rows
    finally:
        c.close()


def _age_seconds(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def _state_for(row: dict) -> str:
    """Return one of: live, imported, stale, unavailable."""
    source = (row.get("provenance") or "").lower()
    if source.startswith(PROVENANCE_PREFIX):
        return "imported"
    age = _age_seconds(row.get("ts"))
    if age is None:
        return "unavailable"          # can't determine age → not live
    if age > 900.0:                   # 15 min staleness bound
        return "stale"
    return "live"


def _overview() -> dict[str, int]:
    """Counts per state — matches the web dashboard cards."""
    rows = _exe("SELECT * FROM measurements")
    live = imported = stale = unavailable = 0
    for r in rows:
        s = _state_for(r)
        if s == "live":
            live += 1
        elif s == "imported":
            imported += 1
        elif s == "stale":
            stale += 1
        else:
            unavailable += 1
    return {"live": live, "imported": imported, "stale": stale,
            "unavailable": unavailable}


def _per_key(limit: int = 20) -> list[dict]:
    """Top N measurements with state, value, source, timestamp."""
    all_rows = _exe("SELECT key, value, provenance, ts FROM measurements "
                    "WHERE value IS NOT NULL ORDER BY rowid DESC LIMIT ?", (limit,))
    out = []
    for r in all_rows:
        out.append({
            "key": r["key"],
            "value": r["value"],
            "state": _state_for(r),
            "source": r.get("provenance", "—"),
            "ts": r.get("ts", "—"),
        })
    return out


# ---------------------------------------------------------------------------
# Tkinter UI
# ---------------------------------------------------------------------------

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "Tkinter is not available on this Python build") from exc


class App(tk.Tk):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()
        self.title("ODB Scanner — Mini Dashboard")
        self.geometry("900x650")
        self.minsize(800, 500)
        self.configure(padx=10, pady=10)

        # ---- top controls ----
        top = tk.Frame(self)
        top.pack(fill="x", pady=(0, 6))

        self.var_vin = tk.StringVar(value="—")
        tk.Label(top, text="VIN:").pack(side="left")
        tk.Label(top, textvariable=self.var_vin, fg="#0b2340",
                 font=("Segoe UI", 10, "bold")).pack(side="left", padx=(4, 0))

        btn_open = tk.Button(top, text="Open web dashboard", command=self._open_web)
        btn_open.pack(side="right")

        btn_sim = tk.Button(top, text="Run simulator self-test", command=self._run_sim)
        btn_sim.pack(side="right", padx=(0, 6))

        # ---- summary cards ----
        mid = tk.Frame(self)
        mid.pack(fill="x", pady=6)
        self.card_vars: dict[str, tk.StringVar] = {}
        for label, key in [("Live", "live"), ("Imported", "imported"),
                           ("Stale", "stale"), ("Unavailable", "unavailable")]:
            frame = tk.Frame(mid)
            frame.pack(side="left", padx=(0, 8))
            tk.Label(frame, text=label, font=("Segoe UI", 9)).pack()
            var = tk.StringVar(value="0")
            self.card_vars[key] = var
            tk.Label(frame, textvariable=var, font=("Segoe UI", 18, "bold"),
                     fg="#0b2340").pack()

        # ---- per-measurement table ----
        tk.Label(self, text=f"Top measurements ({limit_default()} entries)"
                 ).pack(anchor="w", pady=(8, 0))
        cols = ("key", "value", "state", "source", "ts")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=16)
        for c in cols:
            self.tree.heading(c, text=c.title())
            self.tree.column(c, width=120, minwidth=80)
        self.tree.pack(fill="both", expand=True, pady=4)

        # scrollbar
        sb = ttk.Scrollbar(self.tree, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")

        # refresh
        self.refresh()

    @staticmethod
    def _limiter() -> int:
        """Small helper so the lambda below stays tidy."""
        return 20

    def refresh(self) -> None:
        """Reload everything from the database."""
        # ---- vehicle / VIN ----
        row = _exe("SELECT vin FROM vehicles ORDER BY id DESC LIMIT 1")
        if row:
            self.var_vin.set(row[0].get("vin", "—") or "—")
        else:
            self.var_vin.set("—")

        # ---- summary cards ----
        ov = _overview()
        for key, var in self.card_vars.items():
            var.set(str(ov.get(key, 0)))

        # ---- per-key table
        for r in self.tree.get_children():
            self.tree.delete(r)
        for rec in _per_key(self._limiter()):
            state = rec["state"]
            tags = (state,)
            self.tree.insert("", "end", values=(
                rec["key"], rec["value"], state.capitalize(),
                rec["source"][:30] + ("…" if len(rec["source"]) > 30 else ""),
                rec["ts"] or "—",
            ), tags=tags)
        # colour the state column
        self.tree.tag_configure("live", foreground="#136b2c")
        self.tree.tag_configure("imported", foreground="#1b4f8f")
        self.tree.tag_configure("stale", foreground="#7a5d00")
        self.tree.tag_configure("unavailable", foreground="#546e7a")

    def _open_web(self) -> None:
        """Launch the web dashboard in the default browser."""
        webbrowser.open_new("http://127.0.0.1:8000")

    def _run_sim(self) -> None:
        """Run the built-in experiment matrix against the simulator."""
        import subprocess
        subprocess.Popen(
            [r"C:\odb_scanner\.venv\Scripts\python.exe",
             r"C:\odb_scanner\tools\diagnose_meb_path.py",
             "--tcp", "127.0.0.1", "9999"],
            cwd=r"C:\odb_scanner",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # Give it a moment then refresh so the user sees the result
        self.after(2000, self.refresh)


def limit_default() -> int:
    return 20


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
