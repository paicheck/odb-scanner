"""The digital twin's picture of the car: each value observed, inferred or
unknown -- as of any moment.

A twin that blurs what it saw with what it guessed is worse than none, so
every quantity says which it is:

* observed -- the newest successful reading at or before `as_of`, with its
  own timestamp, age, freshness verdict (analysis/freshness.py), provenance
  and the decoder's documentation status. `current` only when it is fresh
  for that quantity (a usable capacity read last week still is; a speed
  read a minute ago is not);
* inferred -- computed from observed values by a rule it names, stamped
  with its oldest input: an inference is never fresher than what it rests
  on;
* unknown -- nothing to go on. Never a zero, never a default.

As of: only rows stamped at or before `as_of` are read, and freshness is
judged at that moment, so the state at 08:00 yesterday is what the twin
would have said at 08:00 yesterday -- nothing recorded later leaks in.
Imported and simulated rows are evidence about some car at some time, not
readings of this one: freshness labels them and they are never current.

Prediction against observation: how the twin's consumption figure did on
the car's recent drives, each predicted from the drives before it
(routing/car.py consumption_check).

Read-only. Nothing here talks to the car.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from analysis.freshness import classify
from database.repository import IMPORTED_PREFIX

# (name, key -- or keys, the newest reading of any wins -- label, unit, scale,
# fresh for seconds)
SPECS = (
    ("soc", "soc_normal", "State of charge (display)", "%", 1.0, 900),
    ("energy", "hv_energy_content", "Energy in the battery", "kWh", 0.001, 900),
    ("usable", "hv_energy_max", "Usable capacity (BMS)", "kWh", 0.001, 30 * 86400),
    ("odometer", "odometer_km", "Odometer", "km", 1.0, 900),
    ("speed", ("speed_kmh", "vehicle_speed"), "Speed", "km/h", 1.0, 120),
    ("battery_temp", "battery_temp", "Battery temperature", "°C", 1.0, 900),
    ("outside_temp", "outside_temp", "Outside temperature", "°C", 1.0, 3600),
    ("resting_12v", "adapter_12v_voltage", "12 V battery at rest", "V", 1.0, 2 * 86400),
    ("aux_12v", ("aux_12v_voltage", "lv_voltage_obd"), "12 V system (car awake)", "V",
     1.0, 900),
)
ADAPTER_KEYS = ("adapter_12v_voltage",)     # the adapter's own reading, not the car's
LOOKBACK = 120                              # rows per key, as analysis/freshness.py
AWAKE_S = 15 * 60          # the car answered this recently: awake
ASLEEP_S = 20 * 60         # the adapter answered this recently: the collector is there
MOVING_KMH = 2.0
CHARGE_RISE_KWH = 0.2      # energy content rose this much with the car standing


@dataclass(frozen=True)
class Quantity:
    name: str
    label: str
    unit: str | None
    kind: str                     # "observed" | "inferred" | "unknown"
    value: object = None
    ts: str | None = None         # when observed; for an inference, its oldest input
    age_s: float | None = None
    state: str | None = None      # freshness verdict for an observed value
    current: bool = False         # good for the value at as_of
    source: str = ""              # the key read, or the rule applied
    decoder: str | None = None    # documentation status of the decoder
    note: str = ""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(ts: str | None) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _rows(repo, vid: int, key: str, until: str, limit: int = LOOKBACK) -> list[dict]:
    """Newest-first rows of one key stamped at or before `until`."""
    return [dict(r) for r in repo.conn.execute(
        "SELECT ts, value, text_value, success, raw_response, provenance, error, "
        "doc_status FROM measurements WHERE vehicle_id=? AND key=? AND ts <= ? "
        "ORDER BY ts DESC, id DESC LIMIT ?", (vid, key, until, limit)).fetchall()]


def _ours(row: dict) -> bool:
    prov = str(row.get("provenance") or "").lower()
    return not prov.startswith(IMPORTED_PREFIX) and prov != "simulated"


def _keys(spec) -> tuple[str, ...]:
    return spec[1] if isinstance(spec[1], tuple) else (spec[1],)


def _observed(repo, vid: int, spec, as_of: datetime) -> tuple[Quantity, dict]:
    """The quantity from whichever of its keys was read most recently, and
    every key's rows (for the mode)."""
    rows, best = {}, None
    for key in _keys(spec):
        q, rows[key] = _observed_key(repo, vid, spec, key, as_of)
        if best is None or (q.kind != "unknown" and (
                best.kind == "unknown" or (q.ts or "") > (best.ts or ""))):
            best = q
    return best, rows


def _observed_key(repo, vid: int, spec, key: str, as_of: datetime
                  ) -> tuple[Quantity, list[dict]]:
    name, _, label, unit, scale, fresh_s = spec
    # Simulated rows describe no car: as if they were not there. Imported ones
    # stay, and freshness labels them evidence, never current.
    rows = [r for r in _rows(repo, vid, key, _iso(as_of))
            if str(r.get("provenance") or "").lower() != "simulated"]
    obs = classify(rows, key, now=as_of, fresh_after_s=fresh_s)
    if not obs.observed or (obs.value is None and obs.text_value is None):
        return Quantity(name, label, unit, "unknown", source=key,
                        note=obs.reason or "never read"), rows
    value = obs.value
    if isinstance(value, (int, float)):
        value = round(float(value) * scale, 3)
    newest = next((r for r in rows if r.get("ts") == obs.ts), {})
    return Quantity(
        name, label, unit, "observed",
        value=value if value is not None else obs.text_value,
        ts=obs.ts, age_s=None if obs.age_s is None else round(obs.age_s),
        state=obs.state.value, current=obs.fresh, source=key,
        decoder=newest.get("doc_status"), note=obs.reason), rows


def _energy_pct(q: dict[str, Quantity]) -> Quantity:
    energy, usable = q["energy"], q["usable"]
    name, label = "energy_pct", "Charge, from energy content"
    if energy.kind != "observed" or usable.kind != "observed" or not usable.value:
        return Quantity(name, label, "%", "unknown", source="energy ÷ usable",
                        note="needs both the energy content and the usable capacity")
    oldest = min((energy, usable), key=lambda x: _parse(x.ts) or datetime.max.replace(
        tzinfo=timezone.utc))
    return Quantity(
        name, label, "%", "inferred",
        value=round(100.0 * float(energy.value) / float(usable.value), 1),
        ts=oldest.ts, age_s=oldest.age_s, current=energy.current and usable.current,
        source="hv_energy_content ÷ hv_energy_max",
        note="the BMS's energy content over its maximum energy; the display "
             "SoC (soc_normal) is the car's own, differently scaled figure")


def _ours_between(rows: list[dict], start: datetime, end: datetime) -> list[tuple]:
    out = []
    for r in rows:
        t = _parse(r.get("ts"))
        if t is None or not (start <= t <= end) or not _ours(r):
            continue
        if r.get("success") in (0, "0") or not isinstance(r.get("value"), (int, float)):
            continue
        out.append((t, float(r["value"])))
    return sorted(out)


def _mode(q: dict[str, Quantity], rows: dict[str, list[dict]], as_of: datetime
          ) -> Quantity:
    """Driving, charging, parked (awake), asleep, or unknown -- with the
    evidence, from this car's own readings only."""
    evidence: list[str] = []

    def heard(keys) -> datetime | None:
        times = [_parse(r["ts"]) for k in keys for r in rows.get(k, [])
                 if _ours(r) and r.get("success") not in (0, "0")]
        times = [t for t in times if t is not None]
        return max(times) if times else None

    car_keys = [k for s in SPECS for k in _keys(s) if k not in ADAPTER_KEYS]
    car_last, adapter_last = heard(car_keys), heard(ADAPTER_KEYS)
    def recent(key, seconds):
        return _ours_between(rows.get(key, []), as_of - timedelta(seconds=seconds), as_of)
    speeds = sorted(recent("speed_kmh", 120) + recent("vehicle_speed", 120))
    odo = recent("odometer_km", 600)
    energy, odo30 = recent("hv_energy_content", 1800), recent("odometer_km", 1800)
    mode = "unknown"
    if speeds and speeds[-1][1] > MOVING_KMH:
        mode = "driving"
        evidence.append(f"speed {speeds[-1][1]:.0f} km/h at {_iso(speeds[-1][0])}")
    elif len(odo) >= 2 and odo[-1][1] - odo[0][1] >= 1.0:
        mode = "driving"
        evidence.append(f"odometer up {odo[-1][1] - odo[0][1]:.0f} km in 10 min")
    elif (len(energy) >= 2 and (energy[-1][1] - energy[0][1]) / 1000.0 >= CHARGE_RISE_KWH
          and (not odo30 or odo30[-1][1] - odo30[0][1] < 1.0)):
        mode = "charging"
        rise = (energy[-1][1] - energy[0][1]) / 1000.0
        evidence.append(f"energy content up {rise:.1f} kWh in 30 min with the "
                        "odometer still")
    elif car_last is not None and (as_of - car_last).total_seconds() <= AWAKE_S:
        mode = "parked"
        evidence.append(f"the car answered at {_iso(car_last)}; not moving or charging")
    elif adapter_last is not None and (as_of - adapter_last).total_seconds() <= ASLEEP_S:
        mode = "asleep"
        evidence.append(f"only the adapter's own 12 V reading, at {_iso(adapter_last)}")
        if car_last:
            evidence.append(f"the car last answered at {_iso(car_last)}")
    else:
        last = max((t for t in (car_last, adapter_last) if t is not None), default=None)
        evidence.append(f"nothing heard since {_iso(last)}" if last else
                        "nothing recorded for this car")
    newest = car_last if mode in ("driving", "charging", "parked") else adapter_last
    return Quantity("mode", "What the car is doing", None,
                    "inferred" if mode != "unknown" else "unknown", value=mode,
                    ts=_iso(newest) if newest and mode != "unknown" else None,
                    current=mode != "unknown", source="speed, odometer, energy content, "
                    "which modules answered", note="; ".join(evidence))


def _parked_since(repo, vid: int, mode: Quantity, as_of: datetime) -> Quantity | None:
    """When the odometer last moved, if the car is standing."""
    if mode.value not in ("parked", "asleep", "charging"):
        return None
    rows = [r for r in _rows(repo, vid, "odometer_km", _iso(as_of), limit=5000)
            if _ours(r) and r.get("success") not in (0, "0")
            and isinstance(r.get("value"), (int, float))]
    if not rows:
        return None
    newest = float(rows[0]["value"])
    since = rows[0]["ts"]
    for r in rows[1:]:
        if float(r["value"]) < newest - 0.05:
            break
        since = r["ts"]
    else:
        return Quantity("parked_since", "Standing since", None, "inferred",
                        value=f"at least {since}", ts=since, source="odometer_km",
                        note="the odometer has not moved in the readings looked at")
    return Quantity("parked_since", "Standing since", None, "inferred", value=since,
                    ts=since, source="odometer_km",
                    note="the first reading at the odometer's current value")


def vehicle_state(repo, vehicle_id: int, as_of: datetime | None = None) -> dict:
    """The twin's state of the car at `as_of` (default now). See the module
    docstring for what observed, inferred and unknown mean."""
    as_of = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    quantities: dict[str, Quantity] = {}
    rows: dict[str, list[dict]] = {}
    for spec in SPECS:
        quantities[spec[0]], spec_rows = _observed(repo, vehicle_id, spec, as_of)
        rows.update(spec_rows)
    quantities["energy_pct"] = _energy_pct(quantities)
    mode = _mode(quantities, rows, as_of)
    quantities["mode"] = mode
    parked = _parked_since(repo, vehicle_id, mode, as_of)
    if parked is not None:
        quantities["parked_since"] = parked
    counts = {k: sum(1 for q in quantities.values() if q.kind == k)
              for k in ("observed", "inferred", "unknown")}
    counts["current"] = sum(1 for q in quantities.values() if q.current)
    return {"as_of": _iso(as_of), "mode": mode.value,
            "quantities": {k: asdict(v) for k, v in quantities.items()},
            "counts": counts}


def prediction_vs_observation(repo, vehicle_id: int, last: int = 10) -> dict:
    """How the consumption figure the Route page uses did on the car's recent
    drives, each predicted only from the drives before it."""
    from routing.car import consumption_check
    check = consumption_check(repo, vehicle_id)
    bt = check["backtest"]
    key = "model_kwh" if check["model"] is not None else "flat_kwh"
    drives = [{"ended": datetime.fromtimestamp(d["end_s"], timezone.utc)
               .isoformat(timespec="seconds"),
               "km": d["km"], "observed_kwh": d["actual_kwh"],
               "predicted_kwh": d[key],
               "error_kwh": round(d[key] - d["actual_kwh"], 3)}
              for d in bt.get("drives", [])[-last:]]
    figure = "this car's own model" if check["model"] is not None else "the flat rate"
    return {"figure": figure, "basis": check["basis"], "drives": drives,
            "flat": bt.get("flat"), "model": bt.get("model"),
            "verdict": bt.get("verdict")}


def describe(state: dict) -> list[str]:
    """One line per quantity, for a person or a prompt: value, kind, age."""
    out = []
    for q in state["quantities"].values():
        if q["kind"] == "unknown":
            out.append(f"{q['label']}: unknown ({q['note']})")
            continue
        unit = f" {q['unit']}" if q["unit"] else ""
        age = "" if q["age_s"] is None else f", {q['age_s'] / 60:.0f} min old"
        flag = "" if q["current"] else ", not current"
        out.append(f"{q['label']}: {q['value']}{unit} ({q['kind']}{age}{flag})")
    return out
