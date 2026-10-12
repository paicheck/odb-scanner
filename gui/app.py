"""Small desktop GUI for the odb_scanner.

A Tkinter window over the same provenance logic the web dashboard uses, plus
the six actions the .bat shortcuts provide. Tkinter is in the standard library,
so this adds no dependency.

TWO THINGS THIS FILE IS CAREFUL ABOUT
-------------------------------------
1. It reads the database and launches the project's OWN entry points. It does
   not talk to an adapter itself -- there is no transport import here, so the
   GUI cannot put a frame on the bus. Every vehicle-facing action is the same
   command the .bat file ran, which means it goes through the same read-only
   choke point (diagnostic/uds.py:validate_request). Integrating the shortcuts
   must not become a way around the safety layer.

2. It delegates the live/imported/stale/unavailable labels to
   analysis/provenance.py rather than re-deriving them. An earlier version
   counted measurement ROWS, which mislabelled 1959 rows of ordinary history as
   "stale" and listed the same imported key six times. That module is tested and
   the web dashboard uses it, so the two views cannot disagree.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Run as `python gui\app.py`, sys.path[0] is the script's own directory
# (...\odb_scanner\gui), NOT the project root -- so `import analysis` fails even
# though the package is right there. The tests never caught this because
# conftest.py inserts the repo root, and so did every headless check: the app
# was only broken when launched the way a user launches it.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import tkinter as tk
    from tkinter import scrolledtext, ttk
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "Tkinter is not available on this Python build") from exc

PY = sys.executable                       # the venv interpreter running this GUI


class _Done:
    """Sentinel: a command finished. Carried on the output queue so the main
    loop performs the UI work -- calling widget methods or `after()` from the
    worker thread is not safe in Tkinter and can deadlock or corrupt state."""

    __slots__ = ("code",)

    def __init__(self, code: int):
        self.code = code


CARDS = (("live", "Live", "read from the car recently"),
         ("imported", "Imported", "Car Scanner export, not read here"),
         ("stale", "Stale", "read once, not refreshed"),
         ("unavailable", "Unavailable", "known to this car, never read"),
         ("unverified", "Unverified scale", "decoder is reverse-engineered"))
STATE_COLOURS = {"live": "#136b2c", "imported": "#1b4f8f",
                 "stale": "#7a5d00", "unavailable": "#546e7a"}
TABLE_LIMIT = 200


# --- the actions the .bat shortcuts provided ----------------------------------
# Each entry: label, argv (after the interpreter), help text, and the verdict
# shown for exit code 0 / non-zero. The verdicts are the same interpretive text
# the .bat files printed, because that text is the useful part -- it names the
# layer that stopped answering instead of leaving a bare exit code.
ACTIONS = [
    {
        "label": "Discover adapters",
        "argv": ["tools/discover_adapters.py"],
        "help": "List and classify every serial interface. Opens nothing and "
                "sends nothing.",
        "ok": "Enumerated. Read the class and evidence column for each port.",
        "fail": "Discovery failed. See the output above.",
        "always_enabled": True,
    },
    {
        "label": "Verify adapter (ATI)",
        "argv": ["tools/discover_adapters.py", "--verify"],
        "help": "Open each candidate and send ATI to confirm it is ELM327-class. "
                "ATI is adapter-local: it reaches no ECU.",
        "ok": "Probed. 'verified' means an adapter answered ATI.",
        "fail": "Probing failed. See the output above.",
        "always_enabled": True,
    },
    {
        "label": "Doctor",
        "argv": ["main.py", "doctor"],
        "help": "Work out which layer stopped answering: COM port, pairing, "
                "pinned protocol, or a sleeping car. Ignition must be ON. "
                "About a minute.",
        "ok": "Everything answered. You can start collecting.",
        "fail": "Something did not answer. The VERDICT above names the layer. "
                "Usual causes: ignition off; adapter.port pointing at the "
                "INCOMING Bluetooth port (try Doctor - all ports); adapter not "
                "fully seated.",
    },
    {
        "label": "Doctor - all ports",
        "argv": ["main.py", "doctor", "--all-ports"],
        "help": "Try every enumerated port. Use when Doctor says the configured "
                "port is missing or nothing answered on it.",
        "ok": "A port answered. Note which one, set it as adapter.port in "
              "config.yaml, then Start collecting.",
        "fail": "No port answered. That points at the adapter or the car, not "
                "the software: check ignition is ON, the adapter is paired in "
                "Windows, and it is fully seated.",
    },
    {
        "label": "Start collecting",
        "argv": ["tools/start_scanner.py"],
        "help": "Run the doctor first, then start the collector and the web "
                "dashboard in the background.",
        "ok": "Started. Dashboard at http://127.0.0.1:8000 (on a tablet, use "
              "http://<this-pc-ip>:8000, same wifi). Stop it with the Stop "
              "button.",
        "fail": "Did not start. If the doctor failed, its verdict is above and "
                "the collector was NOT started. Otherwise check data/logs/ "
                "(collector.log, web.log).",
    },
    {
        "label": "Stop",
        "argv": ["tools/start_scanner.py", "--stop"],
        "help": "Stop the collector and dashboard started by Start collecting, "
                "using the saved pids.",
        "ok": "Stopped. If something still holds the port, close any leftover "
              "consoles running main.py, then check Status.",
        "fail": "Stop reported a problem. See above.",
        "always_enabled": True,
    },
    {
        "label": "Status",
        "argv": ["tools/start_scanner.py", "--status"],
        "help": "What is running right now.",
        "ok": "Reported above. If the collector is alive but no new data "
              "appears, run Doctor - all ports and check the adapter link.",
        "fail": "Status check failed. See above.",
        "always_enabled": True,
    },
    {
        "label": "Smoke test (no car)",
        "argv": ["tools/smoke_test.py"],
        "help": "Run the whole pipeline against the built-in simulator: "
                "simulator, collector, database, analysis, web, read-only "
                "guard. No car and no Ollama needed. About 20 seconds.",
        "ok": "Pipeline OK. Nothing is wrong with the software -- if the real "
              "car will not connect, it is a vehicle or adapter problem, so use "
              "Doctor.",
        "fail": "Something failed. The lines above name what.",
        "always_enabled": True,
    },
]


def load_state():
    """(rows, counts, vin, note) from the configured database.

    `note` carries a reason when the database cannot be read, so the window can
    explain itself instead of showing zeros that look like data. Returns an
    empty result rather than raising: a GUI that crashes on a missing database
    is worse than one that says why it is empty.
    """
    try:
        from analysis.provenance import key_states, summary
        from config import load_config
        from database.repository import Repository
        from decoders.registry import build_default_registry

        cfg = load_config()
        vin = cfg.get("vehicle.vin", "") or ""
        profile = cfg.get("vehicle.did_profile", "eup")
        stale_after = float(cfg.get("collector.max_value_age_s", 900.0))
        registry = build_default_registry(profile)

        repo = Repository(cfg.get("database.path"))
        try:
            vehicle = repo.first_vehicle()
            vid = vehicle["id"] if vehicle else None
            if vehicle and vehicle["vin"]:
                vin = vehicle["vin"]
            rows = key_states(repo, vid, registry, stale_after_s=stale_after)
            return rows, summary(rows), vin, ""
        finally:
            repo.close()
    except Exception as exc:  # noqa: BLE001 - explain, do not crash
        return [], {}, "", f"Could not read the database: {exc}"


class CommandRunner:
    """Run a project command in a thread and stream its output to a queue.

    Tkinter is not thread-safe, so the worker never touches a widget: it pushes
    lines onto a queue and the main loop drains it with after(). A long command
    (the doctor takes about a minute) must not freeze the window, and its output
    must appear as it is produced rather than all at once at the end.
    """

    def __init__(self, out_queue: queue.Queue[object]):
        self.q = out_queue
        self.proc: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None

    @property
    def busy(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, argv: list[str], on_exit) -> bool:
        if self.busy:
            return False
        env = dict(os.environ)
        # Force UTF-8 so a cp1252 console code page cannot turn the doctor's
        # box-drawing and degree characters into a decode error mid-run.
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        self.thread = threading.Thread(
            target=self._run, args=(argv, env, on_exit), daemon=True)
        self.thread.start()
        return True

    def _run(self, argv, env, on_exit) -> None:
        cmd = "$ " + " ".join([os.path.basename(PY)] + argv)
        self.q.put(cmd + "\n")
        code = -1
        try:
            self.proc = subprocess.Popen(
                [PY, *argv], cwd=str(ROOT), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1)
            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                self.q.put(line)
            code = self.proc.wait()
        except Exception as exc:  # noqa: BLE001 - report into the pane
            self.q.put(f"\n[launcher error] {type(exc).__name__}: {exc}\n")
        finally:
            self.proc = None
            self.q.put(f"\n[exit code {code}]\n")
            on_exit(code)

    def terminate(self) -> None:
        """Ask the child to stop. Used when the window closes mid-command."""
        if self.proc is not None:
            try:
                self.proc.terminate()
            except Exception:  # pragma: no cover - best effort
                pass


class App(tk.Tk):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()
        self.title("ODB Scanner")
        self.geometry("1020x720")
        self.minsize(860, 560)
        self.configure(padx=10, pady=10)

        self.q: queue.Queue[object] = queue.Queue()
        self.runner = CommandRunner(self.q)
        self.action_buttons: dict[str, tk.Button] = {}
        self.current_action: dict | None = None

        self._build_header()
        self._build_notebook()
        self.refresh()
        self.after(100, self._drain_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- layout -------------------------------------------------------------
    def _build_header(self) -> None:
        top = tk.Frame(self)
        top.pack(fill="x", pady=(0, 6))
        self.var_vin = tk.StringVar(value="-")
        tk.Label(top, text="VIN:").pack(side="left")
        tk.Label(top, textvariable=self.var_vin, fg="#0b2340",
                 font=("Segoe UI", 10, "bold")).pack(side="left", padx=(4, 0))
        tk.Label(top, text="   read-only diagnostics",
                 fg="#136b2c").pack(side="left")
        tk.Button(top, text="Refresh", command=self.refresh).pack(side="right")
        tk.Button(top, text="Open web dashboard",
                  command=self._open_web).pack(side="right", padx=(0, 6))

        self.var_note = tk.StringVar(value="")
        tk.Label(self, textvariable=self.var_note, fg="#9a3412",
                 wraplength=980, justify="left").pack(fill="x")

    def _build_notebook(self) -> None:
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, pady=(6, 0))
        self._build_data_tab(nb)
        self._build_tools_tab(nb)

    def _build_data_tab(self, nb) -> None:
        tab = tk.Frame(nb)
        nb.add(tab, text="  Measurements  ")

        cards = tk.Frame(tab)
        cards.pack(fill="x", pady=6)
        self.card_vars: dict[str, tk.StringVar] = {}
        for key, label, note in CARDS:
            frame = tk.Frame(cards, bd=1, relief="solid", padx=10, pady=6)
            frame.pack(side="left", padx=(0, 8))
            tk.Label(frame, text=label, font=("Segoe UI", 9)).pack()
            var = tk.StringVar(value="-")
            self.card_vars[key] = var
            tk.Label(frame, textvariable=var, font=("Segoe UI", 20, "bold"),
                     fg=STATE_COLOURS.get(key, "#0b2340")).pack()
            tk.Label(frame, text=note, font=("Segoe UI", 8), fg="#666").pack()

        tk.Label(tab, anchor="w", justify="left", fg="#444",
                 text="One row per measurement key. Per-cell slots are rolled "
                      "up, so 108 cell voltages are one row that says how many "
                      "actually answered. Imported rows are Car Scanner "
                      "evidence, never counted as live."
                 ).pack(fill="x", pady=(4, 2))

        cols = ("key", "value", "state", "source", "ts")
        headings = {"key": "Measurement", "value": "Value", "state": "State",
                    "source": "Source", "ts": "When"}
        widths = {"key": 280, "value": 110, "state": 110, "source": 190,
                  "ts": 175}
        wrap = tk.Frame(tab)
        wrap.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", height=16)
        for c in cols:
            self.tree.heading(c, text=headings[c])
            self.tree.column(c, width=widths[c], minwidth=70, anchor="w")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for state, colour in STATE_COLOURS.items():
            self.tree.tag_configure(state, foreground=colour)

    def _build_tools_tab(self, nb) -> None:
        tab = tk.Frame(nb)
        nb.add(tab, text="  Run tools  ")

        bar = tk.Frame(tab)
        bar.pack(fill="x", pady=(6, 2))
        for action in ACTIONS:
            btn = tk.Button(bar, text=action["label"],
                            command=lambda a=action: self._run_action(a))
            btn.pack(side="left", padx=(0, 6), pady=2)
            self.action_buttons[action["label"]] = btn

        self.var_action_help = tk.StringVar(
            value="Pick an action. These are the same commands the .bat "
                  "shortcuts ran; the GUI adds no new way to reach the car.")
        tk.Label(tab, textvariable=self.var_action_help, fg="#444",
                 wraplength=980, justify="left", anchor="w").pack(fill="x")

        self.var_verdict = tk.StringVar(value="")
        self.verdict_label = tk.Label(tab, textvariable=self.var_verdict,
                                      wraplength=980, justify="left",
                                      anchor="w", fg="#0b2340")
        self.verdict_label.pack(fill="x", pady=(4, 2))

        self.output = scrolledtext.ScrolledText(
            tab, height=22, wrap="none", font=("Consolas", 9),
            background="#101820", foreground="#d7e3ee")
        self.output.pack(fill="both", expand=True, pady=(2, 0))
        self.output.configure(state="disabled")

        foot = tk.Frame(tab)
        foot.pack(fill="x", pady=(4, 0))
        tk.Button(foot, text="Clear output",
                  command=self._clear_output).pack(side="left")
        tk.Label(foot, fg="#666",
                 text="Output streams live. Closing this window terminates a "
                      "running command."
                 ).pack(side="left", padx=(10, 0))

    # -- data ---------------------------------------------------------------
    def refresh(self) -> None:
        rows, counts, vin, note = load_state()
        self.var_vin.set(vin or "-")
        self.var_note.set(note)
        for key, _label, _note in CARDS:
            self.card_vars[key].set(str(counts.get(key, 0)))

        self.tree.delete(*self.tree.get_children())
        for r in rows[:TABLE_LIMIT]:
            value = r.get("value")
            if value is None:
                shown = "-"
            elif isinstance(value, float):
                shown = f"{value:.4g}"
            else:
                shown = str(value)
            unit = r.get("unit") or ""
            if shown != "-" and unit:
                shown = f"{shown} {unit}"
            state = r["state"]
            label = state.capitalize()
            # A rollup must say how much of it answered, or 100 of 108 cell
            # slots reads as complete coverage.
            if r.get("known", 0) > 1:
                label = f"{r.get('live', 0)}/{r['known']} live"
            self.tree.insert("", "end", tags=(state,), values=(
                f"{r.get('name') or r['key']}  ({r['key']})",
                shown, label, r.get("source") or "-", r.get("ts") or "-"))

    # -- actions ------------------------------------------------------------
    def _run_action(self, action: dict) -> None:
        if self.runner.busy:
            self._append("\n[busy] a command is already running; wait for it "
                         "or close the window to terminate it.\n")
            return
        self.current_action = action
        self.var_verdict.set(f"Running: {action['label']} ...")
        self.var_action_help.set(action["help"])
        self._set_buttons_enabled(False)
        self._append(f"\n{'=' * 68}\n{action['label']}\n{'=' * 68}\n")
        started = self.runner.start(action["argv"], self._on_command_exit)
        if not started:
            self._on_command_exit(-1)

    def _on_command_exit(self, code: int) -> None:
        """Runs on the worker thread: touches the queue and nothing else.

        Every widget update happens in _on_done, called from the main loop when
        it drains the sentinel. Calling `after()` or a widget method from a
        worker thread is not safe in Tkinter.
        """
        action = self.current_action or {}
        verdict = action.get("ok" if code == 0 else "fail", "")
        prefix = "OK  " if code == 0 else "FAIL"
        self.q.put(f"\n--- {prefix}: {verdict}\n")
        self.q.put(_Done(code))

    def _on_done(self, code: int) -> None:
        """Main-loop side of a finished command: verdict, buttons, refresh."""
        action = self.current_action or {}
        verdict = action.get("ok" if code == 0 else "fail", "")
        label = action.get("label", "command")
        if code == 0:
            self.var_verdict.set(f"OK - {label}: {verdict}")
            self.verdict_label.configure(fg="#136b2c")
        else:
            self.var_verdict.set(f"FAIL - {label} (exit {code}): {verdict}")
            self.verdict_label.configure(fg="#9a3412")
        self._set_buttons_enabled(True)
        # The command may have written rows (a collection run, an import), so
        # the counts and table are re-read rather than left showing the state
        # from before it started.
        self.refresh()

    def _set_buttons_enabled(self, enabled: bool) -> None:
        for action in ACTIONS:
            btn = self.action_buttons.get(action["label"])
            if btn is None:
                continue
            # Stop and the read-only inspections stay clickable regardless, so a
            # hung doctor can still be stopped and the status checked.
            keep = bool(action.get("always_enabled"))
            btn.configure(state="normal" if (enabled or keep) else "disabled")

    def _drain_queue(self) -> None:
        try:
            while True:
                item = self.q.get_nowait()
                if isinstance(item, _Done):
                    self._on_done(item.code)
                else:
                    self._append(item)
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def _append(self, text: str) -> None:
        self.output.configure(state="normal")
        self.output.insert("end", text)
        self.output.see("end")
        # Cap the pane: a collector left running would otherwise grow it
        # without bound and eventually make the window unresponsive.
        if int(self.output.index("end-1c").split(".")[0]) > 5000:
            self.output.delete("1.0", "1000.0")
        self.output.configure(state="disabled")

    def _clear_output(self) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")
        self.var_verdict.set("")

    def _open_web(self) -> None:
        """Open the dashboard. The token is not appended: the browser either
        has the cookie already or gets the login page that carries it. Putting
        a secret in a launched URL leaks it into process listings."""
        webbrowser.open_new("http://127.0.0.1:8000")

    def _on_close(self) -> None:
        self.runner.terminate()
        self.destroy()


def main(argv: list[str] | None = None) -> int:
    """Entry point. `--selftest` exercises the import and data path headlessly.

    The flag exists because of a bug the widget tests could not see: launched as
    `python gui\app.py`, sys.path[0] is the script's own directory, so
    `import analysis` failed at runtime while every test passed -- conftest and
    each headless check had already put the project root on the path. A
    subprocess launched from a foreign cwd is the only thing that reproduces how
    a user actually starts this, so it is worth an entry point to drive it.
    """
    # The .bat files all begin `cd /d "%~dp0"` for the same reason. config.yaml
    # gives database.path as "data/real_car.db", a RELATIVE path: launched from
    # anywhere but the project root it resolved against the wrong directory,
    # created an empty database there, and the window reported every measurement
    # as "unavailable" -- which reads as "this car never reported the data"
    # rather than "this is not the database". A desktop shortcut has whatever
    # working directory the shell gives it, so this must not depend on the user
    # launching from the right place.
    try:
        os.chdir(ROOT)
    except OSError:  # pragma: no cover - only if the tree moved mid-run
        pass
    args = sys.argv[1:] if argv is None else list(argv)
    if "--selftest" in args:
        rows, counts, vin, note = load_state()
        if note:
            print(f"SELFTEST FAIL: {note}")
            return 1
        print(f"SELFTEST OK vin={vin} keys={len(rows)} counts={counts}")
        return 0
    App().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
