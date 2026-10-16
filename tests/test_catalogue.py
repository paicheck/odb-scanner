"""Tests for the definition catalogue: schema, confidence, provenance, replay.

Everything here is offline. Where a vehicle response would be needed, a
SYNTHETIC fixture is used and is labelled as such in the test name and in the
`Observation.evidence_kind`, so a fixture can never be mistaken for evidence.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.replay import (  # noqa: E402
    EvidenceKind,
    Observation,
    Verdict,
    compare,
    infer_layout,
    layout_admits,
    observations_from_measurements,
    refuted_layouts,
    replay_did_record,
    replay_with_registry,
    scale_candidates,
    to_csv,
    to_json,
)
from decoders.catalogue import (  # noqa: E402
    ByteLayout,
    ByteOrder,
    CATALOGUE_VERSION,
    CatalogueError,
    Confidence,
    EvidenceKind as DefEvidenceKind,
    MeasurementDefinition,
    build_catalogue,
    validate_catalogue,
    validate_definition,
)
from decoders.registry import build_default_registry  # noqa: E402

BE16 = ByteLayout(offset=0, length=2, byte_order=ByteOrder.BIG_ENDIAN, signed=False)
CONTENT = ByteLayout(offset=2, length=2, byte_order=ByteOrder.BIG_ENDIAN, signed=False,
                     scale=25.0)
MAXW = ByteLayout(offset=0, length=2, byte_order=ByteOrder.BIG_ENDIAN, signed=False,
                  scale=50.0)


# --------------------------------------------------------------- catalogue shape
def test_every_registered_spec_produces_a_definition():
    for profile in ("meb", "eup"):
        reg = build_default_registry(profile)
        cat = build_catalogue(profile, reg)
        assert len(cat) == len(reg.all())


def test_definitions_carry_the_required_fields():
    d = build_catalogue("meb")["hv_energy_content"]
    row = d.as_dict()
    for field in ("key", "name", "unit", "confidence", "ecu_key", "request_can_id",
                  "response_can_id", "transport", "session_required", "service",
                  "did", "request_bytes", "byte_offset", "byte_length",
                  "byte_order", "signed", "scale", "formula", "plausible_min",
                  "plausible_max", "plausible_basis", "provenance", "doc_status",
                  "evidence_kind", "evidence_source", "recorded_at",
                  "validation_method", "limitations", "falsified_by", "version"):
        assert field in row, field
        assert row[field] is not None, f"{d.key}.{field} is None"


def test_confidence_values_are_the_documented_three():
    for profile in ("meb", "eup"):
        for d in build_catalogue(profile).values():
            assert d.confidence in (Confidence.VERIFIED, Confidence.PROVISIONAL,
                                    Confidence.UNKNOWN)


def test_catalogue_is_internally_consistent_for_both_profiles():
    for profile in ("meb", "eup"):
        assert validate_catalogue(profile) == []


def test_unknown_fields_stay_unknown_rather_than_being_guessed():
    """A definition with no evidence must not acquire a layout or a range."""
    d = build_catalogue("meb")["charge_mode"]
    assert d.confidence is Confidence.UNKNOWN
    assert d.evidence_kind is DefEvidenceKind.NONE
    assert d.plausible_min is None and d.plausible_max is None


# ------------------------------------------------------------- DID / CAN ids
def test_did_and_can_ids_are_stored_as_integers():
    d = build_catalogue("meb")["hv_energy_content"]
    assert d.did == 0x2AB8
    assert d.service == 0x22
    assert d.request_bytes == "222AB8"
    assert isinstance(d.request_can_id, int)
    assert d.request_can_id == 0x710
    assert d.response_can_id == 0x77A
    # and the flattened form is human-readable hex
    assert d.as_dict()["did"] == "0x2AB8"
    assert d.as_dict()["request_can_id"] == "0x710"


def test_the_two_energy_dids_are_distinct_and_not_conflated():
    cat = build_catalogue("meb")
    c, m = cat["hv_energy_content"], cat["hv_energy_max"]
    assert c.did == 0x2AB8 and m.did == 0x2AB2
    assert c.did != m.did
    assert c.layout != m.layout


# -------------------------------------------------------------- versioning
def test_version_is_stable_across_rebuilds():
    a = build_catalogue("meb")
    b = build_catalogue("meb")
    assert {k: v.version for k, v in a.items()} == {k: v.version for k, v in b.items()}


def test_version_changes_when_the_layout_changes():
    d = build_catalogue("meb")["hv_energy_content"]
    mutated = MeasurementDefinition(
        key=d.key, name=d.name, unit=d.unit, confidence=d.confidence,
        layout=ByteLayout(offset=0, length=2, byte_order=ByteOrder.BIG_ENDIAN,
                          signed=False, scale=25.0))
    assert mutated.ensure_version().version != d.version


def test_version_prefix_names_the_catalogue_release():
    d = build_catalogue("meb")["hv_energy_max"]
    assert d.version.startswith(CATALOGUE_VERSION)


# ------------------------------------------------------------- validation rules
def _replace_layout(defn, layout):
    """A copy of `defn` with a different layout, everything else preserved.

    The negative catalogue tests all need this. Rebuilding the dataclass
    field-by-field meant every one of them forgot `did` or `unit`, so the
    validator tripped on a DIFFERENT check first (did/unit mismatch) and the
    test passed or failed for the wrong reason.
    """
    return MeasurementDefinition(**{**defn.__dict__, "layout": layout, "version": ""})


def test_layout_must_reproduce_the_live_decoder():
    reg = build_default_registry("meb")
    d = build_catalogue("meb", reg)["hv_energy_content"]
    # a wrong offset still decodes, but not to the same number
    bad = _replace_layout(d, ByteLayout(offset=0, length=2,
                                        byte_order=ByteOrder.BIG_ENDIAN,
                                        signed=False, scale=25.0))
    with pytest.raises(CatalogueError, match="live decoder"):
        validate_definition(bad, reg.get("hv_energy_content"))


def test_wrong_unit_is_a_catalogue_error():
    reg = build_default_registry("meb")
    d = build_catalogue("meb", reg)["hv_energy_max"]
    bad = _replace_layout(d, d.layout)
    object.__setattr__(bad, "unit", "kWh")
    with pytest.raises(CatalogueError, match="unit"):
        validate_definition(bad, reg.get("hv_energy_max"))


def test_confidence_without_evidence_is_rejected():
    reg = build_default_registry("meb")
    d = build_catalogue("meb", reg)["hv_energy_content"]
    bad = _replace_layout(d, d.layout)
    object.__setattr__(bad, "evidence_kind", DefEvidenceKind.NONE)
    with pytest.raises(CatalogueError, match="no evidence"):
        validate_definition(bad, reg.get("hv_energy_content"))


def test_a_sources_own_label_is_not_verification():
    reg = build_default_registry("meb")
    d = build_catalogue("meb", reg)["hv_energy_content"]
    bad = _replace_layout(d, d.layout)
    object.__setattr__(bad, "confidence", Confidence.VERIFIED)
    object.__setattr__(bad, "evidence_kind",
                       DefEvidenceKind.THIRD_PARTY_DEFINITION)
    with pytest.raises(CatalogueError, match="not independent verification"):
        validate_definition(bad, reg.get("hv_energy_content"))


def test_inverted_plausible_range_is_rejected():
    reg = build_default_registry("meb")
    d = build_catalogue("meb", reg)["hv_energy_content"]
    bad = _replace_layout(d, d.layout)
    object.__setattr__(bad, "plausible_min", 100.0)
    object.__setattr__(bad, "plausible_max", 1.0)
    with pytest.raises(CatalogueError, match="plausible_min"):
        validate_definition(bad, reg.get("hv_energy_content"))


def test_only_the_captured_gateway_energy_dids_claim_verified():
    """Exactly the two gateway energy DIDs are VERIFIED (2026-10-15 capture).

    A real capture landed on 2026-10-15: a passive HCI capture of Car Scanner
    returned the actual response bytes for 0x2AB8 / 0x2AB2. Everything else in
    the catalogue must remain below VERIFIED until it too has a captured
    response (evidence_kind VEHICLE_RESPONSE).
    """
    verified = {profile: {d.key for d in build_catalogue(profile).values()
                          if d.confidence is Confidence.VERIFIED}
                for profile in ("meb", "eup")}
    expected = {"hv_energy_content", "hv_energy_max"}
    assert verified == {"meb": expected, "eup": expected}, (
        f"unexpected VERIFIED set: {verified}. Only the two gateway energy "
        f"DIDs have a captured vehicle response.")


# ------------------------------------------------------------- replay: decoding
def test_replay_reproduces_the_observed_values_from_integral_raw():
    # 41325 Wh -> raw 1653, 41200 Wh -> raw 1648. SYNTHETIC bytes constructed
    # from the arithmetic, NOT a vehicle capture.
    assert replay_did_record(b"\x00\x00\x06\x75", CONTENT) == 41325.0
    assert replay_did_record(b"\x00\x00\x06\x70", CONTENT) == 41200.0
    assert replay_did_record(b"\x04\x28", MAXW) == 53200.0


def test_replay_offset_is_honoured():
    """SBI=2 means the two leading bytes must not affect the value."""
    assert replay_did_record(b"\xFF\xFF\x06\x75", CONTENT) == 41325.0
    assert replay_did_record(b"\x00\x00\x06\x75", CONTENT) == 41325.0
    assert replay_did_record(b"\x12\x34\x06\x75", CONTENT) == 41325.0


def test_replay_refuses_a_short_record():
    with pytest.raises(ValueError, match=r"needs 4 to read \[2:4\]"):
        replay_did_record(b"\x06", CONTENT)
    with pytest.raises(ValueError, match=r"needs 4 to read \[2:4\]"):
        replay_did_record(b"\x00\x06\x75", CONTENT)
    with pytest.raises(ValueError, match=r"needs 2 to read \[0:2\]"):
        replay_did_record(b"\x04", MAXW)


def test_replay_signed_and_unsigned_differ():
    u = ByteLayout(offset=0, length=2, byte_order=ByteOrder.BIG_ENDIAN, signed=False)
    s = ByteLayout(offset=0, length=2, byte_order=ByteOrder.BIG_ENDIAN, signed=True)
    assert replay_did_record(b"\xFF\x9C", u) == 65436
    assert replay_did_record(b"\xFF\x9C", s) == -100


def test_replay_byte_order_matters():
    be = ByteLayout(offset=0, length=2, byte_order=ByteOrder.BIG_ENDIAN, signed=False)
    le = ByteLayout(offset=0, length=2, byte_order=ByteOrder.LITTLE_ENDIAN, signed=False)
    assert replay_did_record(b"\x06\x75", be) == 1653
    assert replay_did_record(b"\x06\x75", le) == 0x7506


def test_replay_applies_divisor_and_addend():
    lay = ByteLayout(offset=0, length=2, byte_order=ByteOrder.BIG_ENDIAN,
                      signed=False, scale=0.25, addend=-40.0)
    # 0x1000 = 4096; 4096 * 0.25 = 1024; 1024 - 40 = 984
    assert replay_did_record(b"\x10\x00", lay) == pytest.approx(984.0)
    assert replay_did_record(b"\x00\x64", lay) == pytest.approx(-15.0)


def test_replay_matches_the_live_decoder():
    reg = build_default_registry("meb")
    for rec, want in ((b"\x00\x00\x06\x75", 41325.0), (b"\x04\x28", 53200.0)):
        key = "hv_energy_content" if rec == b"\x00\x00\x06\x75" else "hv_energy_max"
        got, why = replay_with_registry(rec, key, reg)
        assert why == ""
        assert got == pytest.approx(want)


def test_replay_with_registry_reports_a_decoder_failure_instead_of_raising():
    reg = build_default_registry("meb")
    got, why = replay_with_registry(b"\x00\x06", "hv_energy_content", reg)
    assert got is None
    assert "IndexError" in why


# -------------------------------------------------- replay: evidence discipline
def _cs(value, key="hv_energy_content"):
    return Observation(key=key, value=value,
                       evidence_kind=EvidenceKind.THIRD_PARTY_REPORTED_VALUE,
                       source="carscanner export")


def test_synthetic_fixtures_never_count_as_corroboration():
    obs = [Observation(key="hv_energy_max", value=53200.0,
                       evidence_kind=EvidenceKind.SYNTHETIC_FIXTURE,
                       raw_hex="04280A64")]
    r = compare("hv_energy_max", MAXW, "v1", obs, build_default_registry("meb"))
    assert r.verdict is Verdict.INDETERMINATE
    assert "synthetic" in r.detail.lower()


def test_no_observations_is_indeterminate_not_pass():
    r = compare("hv_energy_max", MAXW, "v1", [], build_default_registry("meb"))
    assert r.verdict is Verdict.INDETERMINATE


def test_missing_layout_is_indeterminate():
    r = compare("hv_energy_max", None, "v1", [_cs(53200.0, "hv_energy_max")],
                build_default_registry("meb"))
    assert r.verdict is Verdict.INDETERMINATE
    assert "no layout" in r.detail


def test_replay_passes_on_real_evidence_and_fails_on_wrong_bytes():
    reg = build_default_registry("meb")
    good = Observation(key="hv_energy_content", value=41325.0,
                       evidence_kind=EvidenceKind.VEHICLE_RESPONSE,
                       raw_hex="00000675")
    ok = compare("hv_energy_content", CONTENT, "v1", [good], reg)
    assert ok.verdict is Verdict.PASS

    bad = Observation(key="hv_energy_content", value=999.0,
                      evidence_kind=EvidenceKind.VEHICLE_RESPONSE,
                      raw_hex="00000675")
    r = compare("hv_energy_content", CONTENT, "v1", [bad], reg)
    assert r.verdict is Verdict.FAIL
    assert "!= expected" in r.detail


def test_truncated_response_fails_loudly_r_than_returning_a_wrong_number():
    reg = build_default_registry("meb")
    short = Observation(key="hv_energy_content", value=41325.0,
                        evidence_kind=EvidenceKind.VEHICLE_RESPONSE, raw_hex="0675")
    r = compare("hv_energy_content", CONTENT, "v1", [short], reg)
    assert r.verdict is Verdict.FAIL


def test_malformed_hex_is_reported_not_raised():
    reg = build_default_registry("meb")
    junk = Observation(key="hv_energy_max", value=53200.0,
                       evidence_kind=EvidenceKind.VEHICLE_RESPONSE, raw_hex="ZZZZ")
    r = compare("hv_energy_max", MAXW, "v1", [junk], reg)
    assert r.verdict is Verdict.FAIL
    assert "bad hex" in r.detail


def test_compare_flags_a_catalogue_decoder_disagreement():
    reg = build_default_registry("meb")
    off = ByteLayout(offset=0, length=2, byte_order=ByteOrder.BIG_ENDIAN,
                     signed=False, scale=25.0)
    obs = Observation(key="hv_energy_content", value=41325.0,
                      evidence_kind=EvidenceKind.VEHICLE_RESPONSE,
                      raw_hex="00000675")
    r = compare("hv_energy_content", off, "v1", [obs], reg)
    assert r.verdict is Verdict.FAIL
    assert "declared layout" in r.detail


def test_compare_reports_live_decoder_agreement():
    reg = build_default_registry("meb")
    r = compare("hv_energy_max", MAXW, "v1", [_cs(53200.0, "hv_energy_max")], reg)
    assert r.extras["live_decoder_agrees"] is True


# --------------------------------------------------- replay: scale constraints
def test_scale_candidates_are_the_divisors_of_the_gcd():
    assert scale_candidates([41325.0, 41200.0]) == [1, 5, 25]
    assert scale_candidates([53200.0]) == scale_candidates([53200.0])


def test_layout_admits_reports_the_raw_a_layout_would_need():
    assert layout_admits(CONTENT, [41325.0, 41200.0]) == [1653, 1648]
    need = layout_admits(_SUPERSEDED, [41325.0])
    assert not isinstance(need[0], int)


def test_the_old_u32_layout_is_refuted_by_the_observed_values():
    """The refutation that changed the shipping decoder, as a regression test."""
    global _SUPERSEDED
    obs = [_cs(41325.0), _cs(41200.0)]
    out = refuted_layouts("hv_energy_content",
                          {"u32/1310.77": _SUPERSEDED,
                           "u16be[2:4]x25": CONTENT}, obs)
    assert "cannot reproduce" in out["u32/1310.77"]
    assert out["u16be[2:4]x25"].startswith("can reproduce")


def test_the_max_did_does_not_discriminate_between_layouts():
    """Documented limitation: both layouts reach 53,200, so only the content DID
    could exclude the old one. If this ever changes the report must say so."""
    obs = [_cs(53200.0, "hv_energy_max")]
    out = refuted_layouts("hv_energy_max",
                          {"u16be[0:2]x50": MAXW, "u32/1310.77": _SUPERSEDED}, obs)
    assert all(v.startswith("can reproduce") for v in out.values()), out


def test_infer_layout_is_indeterminate_without_a_unique_scale():
    r = infer_layout("hv_energy_content", [_cs(41325.0), _cs(41200.0)])
    assert r.verdict is Verdict.INDETERMINATE
    assert r.extras["admissible_scales"] == [1, 5, 25]


def test_infer_layout_passes_when_one_scale_survives():
    r = infer_layout("hv_energy_content", [_cs(41325.0), _cs(41200.0)],
                     plausible_max=41325.0 / 25 + 1)
    assert r.verdict is Verdict.PASS


def test_infer_layout_with_only_synthetic_data_is_indeterminate():
    obs = [Observation(key="hv_energy_max", value=53200.0,
                       evidence_kind=EvidenceKind.SYNTHETIC_FIXTURE)]
    r = infer_layout("hv_energy_max", obs)
    assert r.verdict is Verdict.INDETERMINATE
    assert "no corroborating" in r.detail


# ----------------------------------------------------- import classification
def test_carscanner_export_rows_are_third_party_not_vehicle_responses():
    class Row(dict):
        pass

    rows = [Row(key="hv_energy_max", ts="2026-10-05T19:02:07+00:00", value=53200.0,
                raw_response="73279.8;[19.Gate] Maximum energy content;53200;Wh",
                decoding_method="carscanner-export")]
    obs = observations_from_measurements(rows, "real_car.db")
    assert len(obs) == 1
    assert obs[0].evidence_kind is EvidenceKind.THIRD_PARTY_REPORTED_VALUE
    assert obs[0].raw_hex is None, "an export line is not hex and must not parse as bytes"


def test_builtin_rows_with_hex_are_treated_as_vehicle_responses():
    class Row(dict):
        pass

    rows = [Row(key="pack_voltage", ts="2026-09-20T11:49:11+00:00", value=357.0625,
                raw_response="5944", decoding_method="builtin")]
    obs = observations_from_measurements(rows, "db")
    assert obs[0].evidence_kind is EvidenceKind.VEHICLE_RESPONSE
    assert obs[0].raw_hex == "5944"


def test_none_valued_rows_are_dropped():
    class Row(dict):
        pass

    rows = [Row(key="k", ts="t", value=None, raw_response="", decoding_method="builtin")]
    assert observations_from_measurements(rows, "db") == []


# ------------------------------------------------------------------- export
def test_csv_export_has_a_row_per_result_and_the_expected_columns():
    obs = [_cs(41325.0)]
    r = infer_layout("hv_energy_content", obs)
    text = to_csv([r])
    assert "key,definition_version,verdict" in text
    assert "hv_energy_content" in text


def test_json_export_round_trips():
    r = infer_layout("hv_energy_content", [_cs(41325.0)])
    import json

    data = json.loads(to_json([r]))
    assert data[0]["key"] == "hv_energy_content"
    assert data[0]["verdict"] in ("PASS", "FAIL", "INDETERMINATE")


def test_csv_escapes_newlines_in_details():
    from analysis.replay import ReplayResult

    r = ReplayResult("k", "v", None, None, Verdict.FAIL, "line one\nline two")
    text = to_csv([r])
    assert "\n" not in text.strip().split("\n")[1]
    # and the reader on the other side still sees one line of detail
    import csv
    import io

    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 1
    assert "line one line two" in rows[0]["detail"]


_SUPERSEDED = ByteLayout(offset=0, length=4, byte_order=ByteOrder.BIG_ENDIAN,
                         signed=False, scale=1.0, divisor=1310.77)
