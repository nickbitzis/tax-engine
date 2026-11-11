from datetime import date

from helpers import calc, cents, combined, mk, rate

from taxengine.calc import Context
from taxengine.certs import Certificate, check, seed_certificates
from taxengine.rules import state_rules

CERT = Certificate("X1", "B1", "Test Outfitters", "resale", ("NY", "OH"), date(2024, 1, 1), date(2026, 3, 31), "valid", "NY-9")


def ctx(*certs):
    c = Context()
    for x in certs:
        c.certificates.setdefault(x.customer_id, []).append(x)
    return c


def test_valid_certificate_exempts_whole_order_including_shipping():
    r = calc(mk("NY", ["TV-55"], shipping=699, customer="B1"), ctx(CERT))
    assert r["tax"] == 0 and r["certificate"]["code"] == "CERT.RESALE"
    assert all(ln["rule_code"] == "CERT.RESALE" for ln in r["lines"])


def test_certificate_valid_on_expiry_day_and_not_after():
    assert check(CERT, "NY", date(2026, 3, 31), state_rules()["NY"]).applies
    r = check(CERT, "NY", date(2026, 4, 1), state_rules()["NY"])
    assert not r.applies and r.code == "CERT.EXPIRED"


def test_expired_certificate_sale_is_taxed_with_reason():
    r = calc(mk("NY", ["TV-55"], on=date(2026, 4, 2), customer="B1"), ctx(CERT))
    assert r["tax"] > 0 and r["certificate"]["code"] == "CERT.EXPIRED"
    assert any("expired 2026-03-31" in n for n in r["notes"])


def test_certificate_not_covering_state():
    r = check(CERT, "NJ", date(2026, 1, 5), state_rules()["NJ"])
    assert not r.applies and r.code == "CERT.STATE_NOT_COVERED"


def test_pending_certificate_does_not_exempt():
    pending = Certificate("X2", "B2", "Test", "government", ("GA",), date(2025, 1, 1), date(2030, 1, 1), "pending", "GA-1")
    assert check(pending, "GA", date(2026, 1, 5), state_rules()["GA"]).code == "CERT.NOT_VALIDATED"


def test_state_that_does_not_accept_certificate_type():
    np = Certificate("X3", "B3", "Test", "nonprofit", ("CA", "OH"), date(2025, 1, 1), date(2030, 1, 1), "valid", "CA-1")
    assert check(np, "CA", date(2026, 1, 5), state_rules()["CA"]).code == "CERT.TYPE_NOT_ACCEPTED"
    assert check(np, "OH", date(2026, 1, 5), state_rules()["OH"]).applies


def test_sale_before_certificate_issued():
    assert check(CERT, "NY", date(2023, 12, 31), state_rules()["NY"]).code == "CERT.NOT_YET_ISSUED"


def test_second_certificate_can_cover_what_the_first_does_not():
    other = Certificate("X4", "B1", "Test Outfitters", "resale", ("NJ",), date(2024, 1, 1), date(2030, 1, 1), "valid", "NJ-2")
    r = calc(mk("NJ", ["TV-55"], customer="B1"), ctx(CERT, other))
    assert r["tax"] == 0 and "X4" in r["certificate"]["message"]


def test_seeded_certificates_have_expiries_and_statuses():
    certs = seed_certificates()
    assert len(certs) == 48
    assert {c.type for c in certs} == {"resale", "nonprofit", "government"}
    assert {"valid", "pending", "rejected"} <= {c.status for c in certs}
    assert any(date(2026, 1, 1) <= c.expires <= date(2026, 10, 31) for c in certs)
    d = certs[5].to_dict(as_of=certs[5].expires)
    assert d["days_to_expiry"] == 0 and d["warning"] == "expires within 60 days"


def test_holiday_item_under_cap_exempt_state_and_local():
    r = calc(mk("FL", [("JEAN-01", 2, None)], on=date(2026, 8, 10)))
    assert r["tax"] == 0 and r["lines"][0]["rule_code"] == "HOLIDAY.EXEMPT"


def test_holiday_item_over_cap_taxable():
    r = calc(mk("FL", ["COAT-01"], on=date(2026, 8, 10)))
    assert r["lines"][0]["rule_code"] == "CLOTH.TAXABLE" and r["tax"] > 0


def test_holiday_window_boundaries():
    assert calc(mk("TN", ["JEAN-01"], on=date(2026, 7, 24)))["tax"] == 0
    assert calc(mk("TN", ["JEAN-01"], on=date(2026, 7, 26)))["tax"] == 0
    assert calc(mk("TN", ["JEAN-01"], on=date(2026, 7, 27)))["tax"] > 0
    assert calc(mk("TN", ["JEAN-01"], on=date(2026, 7, 23)))["tax"] > 0


def test_holiday_state_portion_only():
    r = calc(mk("AL", ["JEAN-01"], on=date(2026, 7, 18)))
    ln = r["lines"][0]
    assert ln["rule_code"] == "HOLIDAY.STATE_ONLY"
    assert r["tax"] == cents(6_800 * combined(("AL-C1", "AL-CITY1", "AL-TD"), date(2026, 7, 18)))
    assert next(j for j in ln["jurisdictions"] if j["kind"] == "state")["tax"] == 0


def test_holiday_without_cap_and_non_holiday_items():
    r = calc(mk("SC", ["LAPT-14", "SKIL-01"], on=date(2026, 8, 8), zk=4))
    assert [ln["rule_code"] for ln in r["lines"]] == ["HOLIDAY.EXEMPT", "GEN.TAXABLE"]
    assert r["lines"][1]["tax"] > 0 and rate("SC") > 0
