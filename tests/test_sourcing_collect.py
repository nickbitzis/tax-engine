from datetime import date

from helpers import calc, cents, combined, mk

from taxengine.calc import Context


def test_origin_state_in_state_sale_uses_warehouse_address():
    r = calc(mk("TX", ["LAMP-01"], zk=4, street="9 Mill St"))
    assert r["sourcing"]["rule"] == "origin" and r["sourcing"]["warehouse"] == "TX-DAL"
    assert [j["id"] for j in r["jurisdictions"]] == ["TX", "TX-C1", "TX-CITY1", "TX-TD"]
    assert r["tax"] == cents(3_999 * combined(("TX", "TX-C1", "TX-CITY1", "TX-TD")))


def test_destination_state_uses_ship_to_address():
    r = calc(mk("WA", ["LAMP-01"], zk=4))
    assert r["sourcing"]["rule"] == "destination"
    assert [j["id"] for j in r["jurisdictions"]] == ["WA", "WA-C2", "WA-CITY3", "WA-SD"]


def test_origin_state_shipped_from_another_state_is_destination_sourced():
    r = calc(mk("AZ", ["LAMP-01"], zk=4))
    assert r["sourcing"]["rule"] == "destination" and "ships from TX" in r["sourcing"]["why"]


def test_pa_switches_to_origin_when_the_pa_warehouse_opens():
    before = calc(mk("PA", ["SPKR-01"], on=date(2026, 4, 15), zk=1))
    after = calc(mk("PA", ["SPKR-01"], on=date(2026, 6, 15), zk=1))
    assert before["sourcing"]["rule"] == "destination" and before["sourcing"]["warehouse"] == "TX-DAL"
    assert after["sourcing"]["rule"] == "origin" and after["sourcing"]["warehouse"] == "PA-ALN"
    assert [j["id"] for j in after["jurisdictions"]] == ["PA", "PA-C2", "PA-CITY3", "PA-SD"]


def test_marketplace_collects_where_facilitator_rule_applies():
    r = calc(mk("FL", ["TV-55"], channel="marketplace"))
    assert r["collector"] == "marketplace" and r["tax"] == 0
    assert all(ln["rule_code"] == "COLLECT.MARKETPLACE" for ln in r["lines"])


def test_marketplace_before_facilitator_rule_seller_collects():
    r = calc(mk("MO", ["LAMP-01"], on=date(2025, 12, 15), channel="marketplace"))
    assert r["collector"] == "seller" and r["tax"] > 0
    assert any("facilitator rule starts" in n for n in r["notes"])
    assert calc(mk("MO", ["LAMP-01"], on=date(2026, 1, 15), channel="marketplace"))["collector"] == "marketplace"


def test_no_sales_tax_state():
    r = calc(mk("OR", ["TV-55"]))
    assert r["collector"] == "none" and r["collector_code"] == "COLLECT.NO_SALES_TAX" and r["tax"] == 0


def test_not_registered_means_not_collected():
    ctx = Context(registrations={"NY": date(2026, 4, 1)})
    r = calc(mk("NY", ["TV-55"]), ctx)
    assert r["collector_code"] == "COLLECT.NOT_REGISTERED" and r["tax"] == 0
    assert "collection starts 2026-04-01" in r["notes"][0]
    assert calc(mk("NY", ["TV-55"], on=date(2026, 4, 1)), ctx)["tax"] > 0
    assert calc(mk("WA", ["TV-55"]), ctx)["collector_code"] == "COLLECT.NOT_REGISTERED"


def test_rate_table_as_loaded_lags_a_late_change():
    on = date(2026, 7, 3)
    correct = calc(mk("KS", ["DESK-01"], on=on))
    loaded = calc(mk("KS", ["DESK-01"], on=on), Context(as_loaded=True))
    assert loaded["tax"] < correct["tax"]
    city = next(j for j in loaded["jurisdictions"] if j["id"] == "KS-CITY1")
    assert city["due_units"] > city["exact_units"]
    later = calc(mk("KS", ["DESK-01"], on=date(2026, 7, 8)), Context(as_loaded=True))
    assert later["tax"] == calc(mk("KS", ["DESK-01"], on=date(2026, 7, 8)))["tax"]


def test_audit_fields_present():
    r = calc(mk("NY", ["LAMP-01"]))
    v = r["rule_versions"]
    assert v["ruleset"] and len(v["fingerprint"]) == 16 and v["state_rule"].startswith("NY-")
    assert set(v["rates"]) == {j["id"] for j in r["jurisdictions"]}
