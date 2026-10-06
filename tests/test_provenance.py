"""provenance.key_states must never make missing data look present.

The dashboard's job here is to say where a number came from, and the failure
that matters is a fabricated or borrowed one. Every test below is about a case
where the tempting shortcut -- treat zero as zero, treat old as current, treat
"the registry says it exists" as "we have it" -- would produce a wrong answer
that reads as working.
"""
from datetime import datetime, timedelta, timezone

import pytest


REG_KEYS = [
    # key, doc_status
    ("soc_abs", "experimentally determined"),
    ("pack_voltage", "experimentally determined"),
    ("cell_voltage_min", "experimentally determined"),
    ("gear", "experimentally determined"),
]


class FakeRegistry:
    """Minimal stand-in exposing the .all() shape key_states uses."""

    class Spec:
        def __init__(self, key, doc_status):
            self.key = key
            self.name = key.replace("_", " ")
            self.unit = "V"
            self.doc_status = doc_status

    def all(self):
        specs = [self.Spec(k, d) for k, d in REG_KEYS]
        # 108 cell slots, as the real meb registry has.
        for i in range(108):
            specs.append(self.Spec(f"cell_v_{i:03d}", "experimentally determined"))
        return specs


@pytest.fixture()
def now():
    return datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)


def stamp(now, seconds_ago: float) -> str:
    return (now - timedelta(seconds=seconds_ago)).isoformat(timespec="seconds")


def make_repo(latest):
    class FakeRepo:
        def latest_measurements(self, vehicle_id):
            return latest
    return FakeRepo()


def test_recent_vehicle_read_is_live(repo, now):
    from analysis.provenance import key_states
    rows = key_states(make_repo({
        "soc_abs": {"provenance": "reported", "value": 81.2,
                    "ts": stamp(now, 30)},
    }), 1, FakeRegistry(), now=now)
    soc = next(r for r in rows if r["key"] == "soc_abs")
    assert soc["state"] == "live"
    assert soc["value"] == 81.2
    assert soc["source"] == "reported"
    assert soc["ts"] == stamp(now, 30)


def test_old_vehicle_read_is_stale_not_live(repo, now):
    """The failure this module exists to prevent: week-old data shown as current.

    latest_measurements() with no age bound returns "the last value that ever
    succeeded", which for a disconnected car is days old. Reporting that as live
    is how a dashboard convinces someone the battery is fine.
    """
    from analysis.provenance import key_states
    rows = key_states(make_repo({
        "pack_voltage": {"provenance": "reported", "value": 431.25,
                         "ts": stamp(now, 4 * 86400)},
    }), 1, FakeRegistry(), now=now)
    pv = next(r for r in rows if r["key"] == "pack_voltage")
    assert pv["state"] == "stale"
    # The value is still shown -- it is real evidence -- but never as live.
    assert pv["value"] == 431.25


def test_imported_row_is_never_live_however_recent(repo, now):
    """An import is evidence, not a reading, and must not become one.

    Otherwise importing a fresh Car Scanner export would make the dashboard
    report live battery data that no request ever produced.
    """
    from analysis.provenance import key_states
    rows = key_states(make_repo({
        "soc_abs": {"provenance": "imported: Car Scanner CSV", "value": 81.2,
                    "ts": stamp(now, 5)},
    }), 1, FakeRegistry(), now=now)
    soc = next(r for r in rows if r["key"] == "soc_abs")
    assert soc["state"] == "imported"
    assert soc["value"] == 81.2


def test_old_imported_row_stays_imported(repo, now):
    """Age does not downgrade imported evidence to stale.

    An export taken last month is still the best available picture of that
    session; calling it "stale" would imply it was once live here, which it
    never was.
    """
    from analysis.provenance import key_states
    rows = key_states(make_repo({
        "soc_abs": {"provenance": "imported: Car Scanner CSV", "value": 81.2,
                    "ts": stamp(now, 30 * 86400)},
    }), 1, FakeRegistry(), now=now)
    soc = next(r for r in rows if r["key"] == "soc_abs")
    assert soc["state"] == "imported"


def test_never_read_is_unavailable_and_carries_no_value(repo, now):
    """Never substitute zero for missing data.

    Phase 12 of the brief is explicit about this, and it is the single easiest
    mistake to make here: a value of 0.0 would satisfy every downstream consumer
    and mean nothing at all.
    """
    from analysis.provenance import key_states
    rows = key_states(make_repo({}), 1, FakeRegistry(), now=now)
    by_key = {r["key"]: r for r in rows}
    assert by_key["gear"]["state"] == "unavailable"
    assert by_key["gear"]["value"] is None
    assert by_key["gear"]["ts"] is None


def test_zero_read_is_live_and_zero_not_missing(repo, now):
    """A real 0 is data, and must not be reported as unavailable.

    The mirror of the previous test: `if not value` would classify a genuine
    0 A discharge reading as missing.
    """
    from analysis.provenance import key_states
    rows = key_states(make_repo({
        "pack_voltage": {"provenance": "reported", "value": 0.0,
                         "ts": stamp(now, 10)},
    }), 1, FakeRegistry(), now=now)
    pv = next(r for r in rows if r["key"] == "pack_voltage")
    assert pv["state"] == "live"
    assert pv["value"] == 0.0


def test_unparseable_timestamp_is_not_treated_as_fresh(repo, now):
    """An unreadable stamp means unknown age, not new data."""
    from analysis.provenance import key_states
    rows = key_states(make_repo({
        "soc_abs": {"provenance": "reported", "value": 81.2,
                    "ts": "not-a-timestamp"},
    }), 1, FakeRegistry(), now=now)
    soc = next(r for r in rows if r["key"] == "soc_abs")
    assert soc["state"] == "stale"


def test_naive_stamp_is_read_as_utc(repo, now):
    """Rows written without an offset must not crash or shift by hours.

    latest_measurements() stores whatever a caller passed; a hand-backfilled or
    imported row can arrive naive.
    """
    from analysis.provenance import key_states
    rows = key_states(make_repo({
        "soc_abs": {"provenance": "reported", "value": 81.2,
                    "ts": "2026-10-06T11:59:30"},
    }), 1, FakeRegistry(), now=now)
    soc = next(r for r in rows if r["key"] == "soc_abs")
    assert soc["state"] == "live"


def test_cell_slots_are_rolled_up_not_listed_108_times(repo, now):
    """108 per-cell specs must not bury the handful of rows a reader checks."""
    from analysis.provenance import key_states
    rows = key_states(make_repo({}), 1, FakeRegistry(), now=now)
    assert not [r for r in rows if r["key"].startswith("cell_v_")]
    group = next(r for r in rows if r["key"] == "cell_voltages")
    assert group["known"] == 108
    assert group["state"] == "unavailable"
    assert group["unavailable"] == 108


def test_cell_group_counts_reachable_and_unreachable_slots(repo, now):
    """A rollup must show what it actually got, not hide partial coverage.

    The cell sweep can answer some slots and refuse others (NRC 0x31 outside the
    real pack), and a rollup that reported only "live" would imply all 108.
    """
    from analysis.provenance import key_states
    latest = {
        f"cell_v_{i:03d}": {"provenance": "reported", "value": 3.99,
                            "ts": stamp(now, 60)}
        for i in range(100)
    }
    rows = key_states(make_repo(latest), 1, FakeRegistry(), now=now)
    group = next(r for r in rows if r["key"] == "cell_voltages")
    assert group["live"] == 100
    assert group["unavailable"] == 8
    assert group["known"] == 108
    assert group["value"] == 3.99


def test_unverified_is_a_flag_not_a_state(repo, now):
    """A current reading from an unproven decoder is live AND unverified.

    Collapsing these onto one axis would force either "live" -- hiding that the
    scale factor is a reverse-engineered guess -- or "unverified" -- hiding that
    the reading is current.
    """
    from analysis.provenance import key_states
    rows = key_states(make_repo({
        "soc_abs": {"provenance": "reported", "value": 81.2,
                    "ts": stamp(now, 10)},
    }), 1, FakeRegistry(), now=now)
    soc = next(r for r in rows if r["key"] == "soc_abs")
    assert soc["state"] == "live"
    assert soc["unverified"] is True


def test_summary_counts_every_row(repo, now):
    from analysis.provenance import key_states, summary
    rows = key_states(make_repo({
        "soc_abs": {"provenance": "reported", "value": 81.2,
                    "ts": stamp(now, 10)},
        "pack_voltage": {"provenance": "imported: Car Scanner CSV",
                         "value": 431.25, "ts": stamp(now, 10)},
    }), 1, FakeRegistry(), now=now)
    counts = summary(rows)
    assert counts["live"] == 1
    assert counts["imported"] == 1
    assert counts["total"] == len(rows)
    assert counts["unverified"] == len(rows)


def test_works_without_an_explicit_now(repo):
    """The dashboard never passes `now`, so the default path must work.

    Every other test here passes a fixed `now` for determinism, which
    short-circuits the default. That hid a `datetime.now(timezinfo=...)` typo
    (the kwarg is `tz`) that only the real page render hit -- the whole route
    500'd on a database with no successful reads.
    """
    from analysis.provenance import key_states, summary
    rows = key_states(make_repo({
        "soc_abs": {"provenance": "reported", "value": 81.2,
                    "ts": datetime.now(tz=timezone.utc).isoformat(
                        timespec="seconds")},
    }), 1, FakeRegistry())
    soc = next(r for r in rows if r["key"] == "soc_abs")
    assert soc["state"] == "live"
    assert summary(rows)["live"] == 1


def test_no_vehicle_yields_unavailable_not_a_crash(repo, now):
    """The dashboard renders before a vehicle is identified; that must not throw."""
    from analysis.provenance import key_states, summary
    rows = key_states(make_repo({}), None, FakeRegistry(), now=now)
    assert rows
    assert all(r["state"] == "unavailable" for r in rows)
    assert summary(rows)["unavailable"] == len(rows)
