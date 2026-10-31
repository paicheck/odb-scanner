# Regression guards for the MEB read path

The 29-bit modules (BMS, DC-DC, vehicle info, electric drive) once went silent on
the car while the whole test suite stayed green. The tests asserted what the
transport *did* (`assert "ATCAF0" in stub.sent`) rather than what the adapter
*accepts*, and the simulator accepted everything, so nothing caught a move to the
hand-framed `ATCAF0` line the field clone refuses with `?`.

These guards close that gap. They fall into two kinds:

* **external truth** — the expected behaviour is a captured wire sequence, not
  the code, so a test cannot bake in a bug; and
* **fidelity** — the offline harness refuses the same things the real adapter
  does, so "works on the simulator" means something.

## What each guard protects

| Guard | File | Protects against |
|---|---|---|
| Captured wire contract | `tests/test_transport_wire_contract.py`, `tests/data/car_scanner_wire.json` | The transport drifting from the 2026-10-15 Car Scanner capture: wrong preamble, a stray `ATCAF0`, hand-framing. |
| Spec ↔ transport parity | `tests/test_transport_wire_contract.py` | `tools/export_spec.py` publishing transport quirks that the transport does not actually obey, so the Android client drifts from the desktop. |
| Clone-strict simulator | `simulator/vehicle.py` (`SimServer(strict=True)`) | The simulator accepting forms the real clone refuses (`ATCAF0`, 8-digit `ATSH`, an >7-byte request under `ATCAF1`). |
| Whole-cycle, per-module | `tests/test_meb_transport_regression.py`, `tools/smoke_test.py` | A physical MEB module silently returning only NULLs, and any forbidden form reaching the wire during a full collector cycle. |
| Android wire contract | `android/app/src/test/kotlin/com/odbscanner/transport/Elm327ProtocolTest.kt` | The Kotlin transport — and therefore the phone — reintroducing a form the Python transport already fixed. |
| Order independence | `tests/conftest.py` (`_restore_experiment_arms`), CI `pytest-randomly` | A test passing or failing because of the order tests ran in, which can hide a real failure. |
| No network in tests | `tests/conftest.py` (`_no_network`) | A test reaching Open-Meteo, a routing provider or an AI service by accident: any real HTTP request through `requests` outside loopback fails as offline. |
| Decoder containment | `tests/test_decoder_fuzz.py` | A short, empty or garbage response raising out of a decoder into the collector, or decoding to NaN/infinity. |
| Shared route vectors | `tests/data/route_vectors.json`, `tools/export_route_vectors.py`, `android/.../PlannerTest.kt` | The phone and the dashboard planning differently -- stops, charger choice, wind energy and arrival ranges are compared case by case. |
| No leakage in the backtest | `tests/test_consumption_backtest.py` | A drive predicted from itself, a later drive or a drive of its own outing, which would flatter any model. |
| A physical consumption model | `tests/test_energy_model.py` | A term going below zero (winter or the motorway looking cheaper), a few drives overriding the physics, a bad reading moving the fit. |
| Methods judged on a known truth | `tests/test_synthetic_journeys.py`, `tools/backtest_consumption.py --synthetic` | The synthetic truth drifting into the shape of a model (it must stay richer than any), or glitches changing the driving. |
| A slow charger is not a slow car | `tests/test_charge_curve.py` | Sessions on a 50 kW charger making the measured curve say the car takes only 50 kW. |
| GPS fixes that may steer | `android/.../GpsGateTest.kt` | A late, vague or jumping fix skipping cues, re-routing or declaring arrival. |

## Changing the wire form is a deliberate act

`tests/data/car_scanner_wire.json` is transcribed from the passive HCI capture
(`docs/investigation/meb_gateway_energy_passive_capture_20261015.md`). If the MEB
read form genuinely changes — because a new capture says so — edit that file *and*
the transport together, in one commit that says why. Do not edit a test
assertion to match the code: that is how the `ATCAF0` regression got in.

## The clone-strict simulator

`SimServer(host, port, strict=True)` reproduces the three refusals observed on
the VEEPEAK clone:

* `ATCAF0` → `?` (Car Scanner's captured sequence is `ATCAF1` throughout);
* an 8-digit `ATSH` → `?` (MEB modules are addressed `ATCP` + 6 digits);
* a request longer than 7 bytes while auto-formatting is on → `?` (the adapter
  adds the ISO-TP PCI byte itself).

Strict mode is opt-in, not the default: the experiment-harness contract tests in
`tests/test_meb_path.py` deliberately send the forms the real clone refuses, to
prove the harness can build them. `tools/smoke_test.py` and the whole-cycle
regression test run the collector against the strict simulator.

## Running them

```
python -m pytest tests/test_transport_wire_contract.py
python -m pytest tests/test_meb_transport_regression.py -m slow
python tools/smoke_test.py
cd android && gradlew.bat :app:testDebugUnitTest
```

CI (`.github/workflows/ci.yml`) runs the Python suite with `pytest-randomly`
(so order dependencies surface) plus the smoke test on push/PR to `main`. The
Android JVM test is not in CI yet — it needs a JDK and the Android SDK.
