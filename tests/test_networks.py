"""The networks to pick from on the Route page (routing/networks.py): counted
from Open Charge Map's listings, most locations first, with what is not a
usable network left out. Listings are synthetic; nothing is fetched."""
import pytest

from routing import networks, providers
from test_route_planner import KEYS, _client, db, services  # noqa: F401

REFERENCE = {"Operators": [
    {"ID": 1, "Title": "(Unknown Operator)"},
    {"ID": 45, "Title": "(Business Owner at Location)"},
    {"ID": 3534, "Title": "IONITY GmbH"}, {"ID": 3296, "Title": "InstaVolt Ltd"},
    {"ID": 32, "Title": "BP Pulse (UK)"},
    {"ID": 23, "Title": "Tesla (Tesla-only charging)"}]}


def poi(op, usage=1, status=50):
    return {"OperatorID": op, "UsageTypeID": usage, "StatusTypeID": status}


GB = ([poi(3296)] * 3 + [poi(3534)] * 2 + [poi(32), poi(32, status=100), poi(1), poi(45),
      poi(None), poi(23), poi(3534, usage=2), poi(3534, status=150)])


def test_most_locations_first_and_only_real_usable_networks():
    got = networks.count([GB], networks.names(REFERENCE))
    assert got == [("InstaVolt Ltd", 3), ("IONITY GmbH", 2), ("BP Pulse (UK)", 1)]


class FakeOcm:
    def __init__(self):
        self.calls = 0

    def locations_in(self, country):
        self.calls += 1
        return GB if country == "GB" else [poi(3534)]

    def reference_data(self):
        return REFERENCE


def test_fetched_once_a_month_per_countries():
    networks._cache.clear()
    ocm = FakeOcm()
    a = networks.networks(ocm, "GB", 50, now=0)
    assert networks.networks(ocm, "GB", 50, now=10 * 86400) == a and ocm.calls == 1
    networks.networks(ocm, "GB", 50, now=31 * 86400)
    assert ocm.calls == 2
    both = networks.networks(ocm, "GB,FR", 50, now=31 * 86400)
    assert dict(both)["IONITY GmbH"] == 3          # two in GB, one in FR
    assert networks.networks(FakeOcm(), "", 50, now=0)   # no countries: GB
    networks._cache.clear()


@pytest.fixture()
def listed(monkeypatch):
    networks._cache.clear()
    monkeypatch.setattr(providers.OpenChargeMap, "locations_in", lambda self, c: GB)
    monkeypatch.setattr(providers.OpenChargeMap, "reference_data", lambda self: REFERENCE)
    yield
    networks._cache.clear()


def test_the_route_page_offers_them_most_first(db, listed):  # noqa: F811
    body = _client(db, KEYS).get("/route").text
    assert 'class="net-pick" data-add-to="prefer"' in body
    assert 'data-add-to="avoid_nets"' in body
    first = body.index("InstaVolt Ltd (3)")
    assert first < body.index("IONITY GmbH (2)") < body.index("BP Pulse (UK) (1)")
    assert "ocm-secret-key" not in body


def test_without_open_charge_map_the_page_still_works(db, monkeypatch):  # noqa: F811
    networks._cache.clear()

    def down(self, c):
        raise providers.RoutingError("Could not reach api.openchargemap.io")
    monkeypatch.setattr(providers.OpenChargeMap, "locations_in", down)
    body = _client(db, KEYS).get("/route").text
    assert "Prefer networks" in body and "net-pick" not in body
    assert "net-pick" not in _client(db).get("/route").text     # no key: no list
