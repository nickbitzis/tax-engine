import pytest
from fastapi.testclient import TestClient

from taxengine.api import create_app
from taxengine.store import Store

CART = {"date": "2026-03-10", "ship_to": {"state": "NY", "zip": "10002", "street": "120 Main St"},
        "lines": [{"sku": "COAT-01"}, {"sku": "TEE-01", "qty": 2}], "shipping": 699, "nexus": "all"}


@pytest.fixture(scope="module")
def client(sim):
    store = Store(":memory:")
    with TestClient(create_app(sim, store)) as c:
        c.store = store
        yield c


def test_healthz_and_metrics(client):
    assert client.get("/healthz").json()["status"] == "ok"
    client.post("/v1/tax/calculate", json=CART)
    m = client.get("/metrics").text
    assert "taxengine_calculations_total" in m and "taxengine_nexus_states" in m and 'taxengine_http_requests_total{route="/healthz"' in m


def test_calculate_returns_explanation_and_writes_audit(client):
    r = client.post("/v1/tax/calculate", json=CART, headers={"x-request-id": "req-1"})
    assert r.status_code == 200 and r.headers["x-request-id"] == "req-1"
    d = r.json()
    assert d["tax"] > 0 and d["sourcing"]["lookup"] == "street_override"
    assert [ln["rule_code"] for ln in d["lines"]] == ["CLOTH.OVER_THRESHOLD", "CLOTH.UNDER_THRESHOLD", "SHIPPING.TAXABLE"]
    rows = client.get("/v1/audit?limit=5").json()["rows"]
    row = next(x for x in rows if x["seq"] == d["audit_seq"])
    assert row["request_id"] == "req-1" and row["tax"] == d["tax"] and row["state_rule"] == d["rule_versions"]["state_rule"]
    assert row["rates"] == d["rule_versions"]["rates"]


def test_audit_log_is_append_only(client):
    client.post("/v1/tax/calculate", json=CART)
    with pytest.raises(Exception, match="append-only"):
        client.store.x("DELETE FROM audit")
    with pytest.raises(Exception, match="append-only"):
        client.store.x("UPDATE audit SET tax = 0")


def test_simulated_nexus_mode_skips_unregistered_states(client):
    body = {**CART, "ship_to": {"state": "WY", "zip": "82001", "street": ""}, "nexus": "simulated"}
    d = client.post("/v1/tax/calculate", json=body).json()
    assert d["collector_code"] == "COLLECT.NOT_REGISTERED" and d["tax"] == 0


def test_invalid_cart_errors(client):
    bad = {**CART, "lines": [{"sku": "NOPE"}]}
    r = client.post("/v1/tax/calculate", json=bad)
    assert r.status_code == 422 and r.json()["error"] == "invalid_cart"
    r = client.post("/v1/tax/calculate", json={**CART, "ship_to": {"state": "NY", "zip": "75001"}})
    assert r.status_code == 422 and "not NY" in r.json()["message"]


def test_lookup_shows_zip_only_difference(client):
    d = client.get("/v1/lookup", params={"state": "NY", "zip": "10002", "street": "120 Main St"}).json()
    assert d["differs_from_zip_only"] and d["combined_rate"] != d["zip_only"]["combined_rate"]


def test_certificates_list_add_and_check(client):
    d = client.get("/v1/certificates", params={"as_of": "2026-11-10"}).json()
    assert d["count"] == 48 and d["expired"] > 0
    new = {"customer_id": "B900", "customer_name": "Ironbark Supply", "type": "resale", "states": ["tx", "ok"],
           "issued": "2026-01-01", "expires": "2027-01-01", "number": "TX-55", "status": "valid"}
    r = client.post("/v1/certificates", json=new)
    assert r.status_code == 201 and r.json()["states"] == ["OK", "TX"]
    cid = r.json()["id"]
    chk = client.get(f"/v1/certificates/{cid}/check", params={"state": "TX", "on": "2026-06-01"}).json()
    assert chk["applies"]
    calc = client.post("/v1/tax/calculate", json={**CART, "ship_to": {"state": "TX", "zip": "75001"}, "customer_id": "B900"}).json()
    assert calc["tax"] == 0 and calc["certificate"]["code"] == "CERT.RESALE"
    bad = {**new, "expires": "2025-01-01"}
    assert client.post("/v1/certificates", json=bad).status_code == 422


def test_nexus_dashboard(client):
    d = client.get("/v1/nexus").json()
    assert len(d["states"]) == 51 and d["crossed_in_period"] >= 5
    assert d["states"][0]["status"] in ("crossed_pending_registration", "approaching", "registered")
    assert any(a["kind"] == "crossed" for a in d["alerts"])
    one = client.get("/v1/nexus/TX").json()
    assert one["collection_start"] == "2018-01-01" and len(one["timeline"]) >= 12


def test_returns_endpoints(client):
    d = client.get("/v1/returns", params={"month": "2026-03"}).json()
    assert d["count"] > 10
    r = client.get("/v1/returns/MI/2026-Q3").json()
    assert r["frequency"] == "quarterly" and r["jurisdictions"]
    csv_r = client.get("/v1/returns/TX/2026-03", params={"format": "csv"})
    assert csv_r.headers["content-type"].startswith("text/csv") and "jurisdiction_id" in csv_r.text
    assert client.get("/v1/returns/KS/2026-03").status_code == 422
    assert client.get("/v1/returns", params={"month": "2024-01"}).status_code == 422


def test_reconciliation_rules_meta_ui(client):
    rec = client.get("/v1/reconciliation").json()
    assert rec["difference"] == rec["rate_lag"] + rec["rounding"]
    rules = client.get("/v1/rules/NY").json()
    assert rules["clothing"] == "below_threshold_exempt" and "Illustrative" in rules["disclaimer"]
    meta = client.get("/v1/meta").json()
    assert len(meta["examples"]) >= 50 and "not current law" in meta["disclaimer"]
    assert client.get("/v1/examples/G08").json()["ship_to"]["street"] == "120 Main St"
    assert client.get("/v1/examples/NOPE").status_code == 404
    html = client.get("/").text
    assert "Cart calculator" in html and "prefers-color-scheme: dark" in html
    assert client.get("/v1/rules/ZZ").status_code == 422
