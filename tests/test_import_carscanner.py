"""Regression tests for tools/import_carscanner.py.

Six tests, six defects. Each was run against the pre-fix importer and failed
for the reason its docstring gives; the two candidates that passed against
pre-fix code -- wrong-unit refusal and re-run idempotency -- describe
behaviour that was correct from the first draft and so were not kept, since
they would be guards presented as regressions.

The importer's job is narrow: take a Car Scanner export, which is the only
real-car evidence available while COM3 is held by the phone, and store it with
provenance nobody can mistake for a live reading. Every defect below was
silent -- the tool reported success while dropping or mislabelling data.
"""
import textwrap


def write_export(tmp_path, body: str) -> str:
    """A minimal Car Scanner export: ';' delimited, every field quoted."""
    header = '"SECONDS";"PID";"VALUE";"UNITS";\n'
    p = tmp_path / "2026-10-05 20-01-08.csv"
    p.write_text(header + textwrap.dedent(body), encoding="utf-8")
    return str(p)


def run(capsys, tmp_path, csv_path, *extra) -> tuple[int, str]:
    from tools.import_carscanner import main
    argv = [csv_path, "--db", str(tmp_path / "import.db"),
            "--vin", "WVWZZZE1ZMP087053", *extra]
    rc = main(argv)
    return rc, capsys.readouterr().out


def test_ecu_tag_prefix_may_be_alphanumeric():
    """A vendor tag whose prefix contains letters is still a vendor tag.

    The pattern anchored on \\d+ before the dot, so it matched "[19.Gate]" and
    "[8105.DC/DC Converter]" but not "[8C.BMS]" -- 8C is alphanumeric. 8C is
    the BMS, where pack voltage, both SoC readings and both pack currents
    live, so the match failure sent the most trustworthy readings in the file
    down the generic path.
    """
    from tools.import_carscanner import classify

    for name, want_tag in (
        ("[8C.BMS] DC Battery voltage", "8C.BMS"),
        ("[19.Gate] HV Battery energy content", "19.Gate"),
        ("[8105.DC/DC Converter] DC-DC current", "8105.DC/DC Converter"),
        ("[01.ENG] Motor RPM", "01.ENG"),
    ):
        kind, tag, bare = classify({"name": name})
        assert kind == "vendor", f"{name!r} was not recognised as vendor-specific"
        assert tag == want_tag
        assert bare and "[" not in bare, "the tag should be stripped from the name"


def test_bms_reading_is_imported_not_excluded_as_generic(tmp_path, capsys):
    """Pack voltage must be planned for import, not written off as generic.

    With the tag pattern failing to match, classify() called this row
    "generic", and generic rows are excluded by default because most generic
    OBD-II PIDs are meaningless on an EV. So the importer reported success
    while importing zero BMS readings -- the failure mode here is a dry run
    that claims 0 rows instead of 1, with the voltage listed under
    "Excluded generic OBD-II PIDs".
    """
    csv_path = write_export(tmp_path, """
        "100.0";"[8C.BMS] DC Battery voltage";"431.75";"V";
        "101.0";"Hybrid/EV Battery System Voltage";"1023.984";"V";
    """)
    rc, out = run(capsys, tmp_path, csv_path, "--dry-run")

    assert rc == 0
    assert "Would import 1 rows" in out, (
        "the [8C.BMS] row was not planned for import:\n" + out)
    assert "pack_voltage" in out, "the cross-check should show pack_voltage"
    # The genuinely bogus generic PID stays excluded, proving the row above
    # was excluded for being vendor-specific, not merely counted twice.
    assert "Excluded generic OBD-II PIDs" in out
    assert "Hybrid/EV Battery System Voltage" in out


def test_mapped_reading_with_no_unit_is_refused(tmp_path, capsys):
    """A mapped row whose unit the export omits is refused, not imported.

    The check was `if row["unit"] and row["unit"] != expected_unit`, so a blank
    unit skipped the guard entirely and the value was stored unverified. The
    unit is the only thing tying the exported number to the scale this project
    stores it in; without it the mapping cannot be checked for that row.
    """
    csv_path = write_export(tmp_path, """
        "100.0";"[8C.BMS] DC Battery voltage";"431.75";"";
    """)
    rc, out = run(capsys, tmp_path, csv_path, "--dry-run")

    assert rc == 0
    assert "Would import 0 rows" in out, (
        "a row with no unit should not be imported:\n" + out)
    assert "export gives no unit" in out, (
        "the refusal should say which check failed:\n" + out)


def test_unmapped_vendor_reading_is_reported_not_dropped(tmp_path, capsys):
    """Vendor readings this project cannot decode are listed, never discarded.

    Car Scanner reads DIDs whose underlying identifiers are still unknown --
    the [19.Gate] block is future work. Reporting them is how a missing mapping
    becomes visible instead of quietly shrinking the import.

    The first assertion also pins defect 1 to a reading with no mapping: an
    alphanumeric tag must reach the "No mapping" report, not be filed under
    generic OBD-II and excluded, which is where the digits-only pattern sent
    every 8C.BMS name.
    """
    csv_path = write_export(tmp_path, """
        "100.0";"[8C.BMS] Battery max SoC cell %";"81.14";"%";
        "100.0";"[19.Gate] 12V Battery SoC";"95";"%";
        "101.0";"[51.ElDrive] Inverter power loss phase V";"0";"W";
    """)
    rc, out = run(capsys, tmp_path, csv_path, "--dry-run")

    assert rc == 0
    assert "No mapping" in out
    assert "[8C.BMS] Battery max SoC cell %" in out, (
        "a vendor reading with no mapping must be reported as such:\n" + out)
    assert "Excluded generic OBD-II PIDs" not in out, (
        "nothing here is a generic OBD-II PID:\n" + out)
    assert "Inverter power loss phase V" in out
    # 12V Battery SoC has an EXTRA_MAPPINGS entry, so it is kept.
    assert "gate_lv_soc" in out


def test_mapped_row_is_stored_under_the_registry_key(tmp_path, capsys):
    """An invented key would hide the cross-check it was meant to enable.

    The mapping table is only useful if the key it names exists in the decode
    registry -- the cross-check joins on it. Entries pointing at
    hv_energy_nominal, lv_voltage and dcdc_lv_voltage named keys no DID
    registers, so those readings were reported as "new keys" and never
    compared against the decoder, while hv_energy_max and aux_12v_voltage,
    which do exist, went unused.
    """
    csv_path = write_export(tmp_path, """
        "100.0";"[19.Gate] Maximum energy content of the traction battery";"53200";"Wh";
        "100.0";"[19.Gate] 12V Battery voltage";"14.52";"V";
        "100.0";"[19.Gate] DC-DC converter low voltage";"14.6";"V";
    """)
    rc, out = run(capsys, tmp_path, csv_path, "--dry-run")

    assert rc == 0
    for real in ("hv_energy_max", "aux_12v_voltage", "dcdc_voltage"):
        assert real in out, f"{real} is in the registry but was not checked"
    for invented in ("hv_energy_nominal", "lv_voltage", "dcdc_lv_voltage"):
        assert invented not in out, f"{invented} is not a registered key"


def test_imported_rows_are_labelled_and_carry_their_source(tmp_path, capsys):
    """Imported rows are indistinguishable from live ones only by accident.

    Provenance and decoding_method are what mark the row as imported; the ecu
    column keeps which module Car Scanner read from, which is the sole record
    distinguishing "[19.Gate] DC-DC converter current" from
    "[8105.DC/DC Converter] DC-DC current" -- the export carries no DID.
    """
    csv_path = write_export(tmp_path, """
        "100.0";"[8C.BMS] DC Battery voltage";"431.75";"V";
        "100.0";"[8105.DC/DC Converter] DC-DC current";"17.8125";"A";
    """)
    rc, out = run(capsys, tmp_path, csv_path)
    assert rc == 0
    assert "Imported 2 rows" in out, out

    from database.repository import Repository
    repo = Repository(str(tmp_path / "import.db"))
    try:
        rows = repo.conn.execute(
            "SELECT key, ecu, provenance, doc_status, decoding_method, "
            "raw_response FROM measurements ORDER BY key").fetchall()
    finally:
        repo.close()

    assert len(rows) == 2
    assert {r["provenance"] for r in rows} == {"imported: Car Scanner CSV"}
    assert {r["doc_status"] for r in rows} == {"inferred"}
    assert {r["decoding_method"] for r in rows} == {"carscanner-export"}
    by_key = {r["key"]: r for r in rows}
    assert by_key["pack_voltage"]["ecu"] == "8C.BMS"
    assert by_key["dcdc8105_current"]["ecu"] == "8105.DC/DC Converter"
    # The original line survives so the SECONDS offset can be recomputed.
    assert "[8C.BMS] DC Battery voltage;431.75;V" in \
        by_key["pack_voltage"]["raw_response"]
