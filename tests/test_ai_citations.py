"""Evidence citations: every key@timestamp in an AI report is looked up in
the evidence the model was given. Invented ones are flagged, never trusted.
No test reaches a model: the AI is a stub."""
from datetime import datetime, timedelta, timezone

from ai import citations
from config import Config
from database.repository import Repository

VIN = "WVWZZZE1ZMS000001"

CONTEXT = {
    "vehicle_state": {"quantities": {
        "soc": {"source": "soc_normal", "kind": "observed", "value": 64.0,
                "ts": "2026-10-30T09:59:00+00:00"}}},
    "dtcs": {"codes": [{"code": "P0AA6", "ecu": "bms",
                        "last_seen": "2026-10-29T08:00:00Z"}]},
    "series": [{"key": "cell_delta_mv", "ts": "2026-10-28T12:00:00+00:00", "value": 14}],
}


def test_the_index_holds_every_reading_under_its_key_and_name():
    got = citations.index(CONTEXT)
    assert got[("soc_normal", "2026-10-30T09:59:00+00:00")] == 64.0
    assert ("soc", "2026-10-30T09:59:00+00:00") in got
    assert ("P0AA6", "2026-10-29T08:00:00+00:00") in got
    assert got[("cell_delta_mv", "2026-10-28T12:00:00+00:00")] == 14


def test_a_citation_in_the_evidence_is_verified_with_the_evidence_value():
    report = ("Charge was 64 % (soc_normal@2026-10-30T09:59:00Z); the code "
              "P0AA6@2026-10-29T08:00:00+00:00 is stored. "
              "Again soc_normal@2026-10-30T09:59:00Z.")
    got = citations.check(report, citations.index(CONTEXT))
    assert got["cited"] == 2 and got["unsupported"] == []
    assert {"key": "soc_normal", "ts": "2026-10-30T09:59:00+00:00",
            "value": 64.0} in got["verified"]


def test_an_invented_citation_is_unsupported_and_says_why():
    report = ("Cells drifted (cell_delta_mv@2026-10-27T12:00:00Z) and the "
              "inverter overheated (inverter_temp@2026-10-28T12:00:00Z).")
    got = citations.check(report, citations.index(CONTEXT))
    whys = {u["key"]: u["why"] for u in got["unsupported"]}
    assert whys["cell_delta_mv"].startswith("no reading of this key at that moment")
    assert whys["inverter_temp"] == "this key is not in the evidence"
    lines = citations.caveats(got)
    assert len(lines) == 2 and all("unsupported" in line for line in lines)


def test_a_report_citing_nothing_is_said_to_cite_nothing():
    got = citations.check("The car is fine.", citations.index(CONTEXT))
    assert got["cited"] == 0
    assert "cites no evidence" in citations.caveats(got)[0]


# --- through the AI service ---------------------------------------------------------

def _cfg():
    return Config({"ai": {}, "ollama": {"host": "http://localhost:11434",
                                        "model": "qwen2.5:7b"}})


def test_the_service_checks_the_report_against_what_it_sent(tmp_path):
    from ai.service import AnalysisService
    repo = Repository(str(tmp_path / "a.db"))
    vid = repo.ensure_vehicle(VIN, make="VW", model="ID.3")
    ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="seconds")
    repo.record_measurement(vid, ts, "soc_normal", "bat_mgmt", "UDS-0x22", "0x0000", "",
                            "reported", "builtin", "verified", "64", 64.0)
    repo.conn.commit()
    sent = {}

    class Stub:
        sends_offsite, where, model = False, "stub", "stub-1"

        def generate(self, prompt, system=None, temperature=None):
            sent["prompt"] = prompt
            return (f"Charge 64 % (soc_normal@{ts}). Pack hot "
                    "(battery_temp@2026-10-30T10:00:00Z).\n\nHYPOTHESES\n- none\n\n"
                    "LIMITATIONS\n- synthetic")

    svc = AnalysisService(_cfg(), repo)
    svc.llm = Stub()
    result = svc.ask("How is the battery?")
    assert "Evidence discipline" in sent["prompt"]
    assert "vehicle_state" in sent["prompt"]
    assert [c["key"] for c in result["citations"]["verified"]] == ["soc_normal"]
    assert "Cited battery_temp@2026-10-30T10:00:00Z: this key is not in the evidence" \
        in result["report"]
    stored = repo.conn.execute("SELECT report_text FROM llm_reports").fetchone()[0]
    assert "battery_temp@2026-10-30T10:00:00Z" in stored.split("AUTOMATIC CAVEATS")[1]
    repo.close()
