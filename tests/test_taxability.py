from fractions import Fraction

from helpers import calc, cents, combined, mk, rate

from taxengine.rules import state_rules

NY1 = ("NY", "NY-C1", "NY-CITY1", "NY-TD")
TX1 = ("TX", "TX-C1", "TX-CITY1", "TX-TD")


def test_general_merchandise_taxable_at_combined_rate():
    r = calc(mk("NY", ["SPKR-01"]))
    assert r["lines"][0]["rule_code"] == "GEN.TAXABLE"
    assert r["tax"] == cents(7_999 * combined(NY1))


def test_prescriptions_exempt():
    r = calc(mk("NY", [("RX-PEN", 2, None)]))
    assert r["tax"] == 0 and r["lines"][0]["rule_code"] == "RX.EXEMPT"


def test_clothing_exempt_state():
    r = calc(mk("PA", ["COAT-01"]))
    assert r["tax"] == 0 and r["lines"][0]["rule_code"] == "CLOTH.EXEMPT"


def test_clothing_taxable_by_default():
    r = calc(mk("TX", ["COAT-01"], ship_from="TX-DAL"))
    assert r["lines"][0]["rule_code"] == "CLOTH.TAXABLE"
    assert r["tax"] == cents(24_900 * combined(TX1))


def test_clothing_under_threshold_exempt_per_item_not_per_line():
    # 3 shirts at $18 = $54 total, each under $110: still exempt
    r = calc(mk("NY", [("TEE-01", 3, None)]))
    assert r["tax"] == 0 and r["lines"][0]["rule_code"] == "CLOTH.UNDER_THRESHOLD"


def test_clothing_threshold_boundary():
    r = calc(mk("NY", [("SHOE-01", 1, 11_000), ("SHOE-01", 1, 10_999)]))
    assert [ln["rule_code"] for ln in r["lines"]] == ["CLOTH.OVER_THRESHOLD", "CLOTH.UNDER_THRESHOLD"]
    assert r["lines"][1]["tax"] == 0 and r["lines"][0]["tax"] > 0


def test_clothing_excess_over_threshold_taxes_only_the_excess():
    t = state_rules()["MA"].clothing_threshold
    r = calc(mk("MA", [("COAT-01", 2, None)]))
    ln = r["lines"][0]
    assert ln["rule_code"] == "CLOTH.EXCESS_TAXABLE"
    assert ln["taxable_amount"] == (24_900 - t) * 2
    assert r["tax"] == cents((24_900 - t) * 2 * rate("MA"))


def test_groceries_exempt_by_default():
    assert calc(mk("NY", ["COFF-2"]))["tax"] == 0


def test_groceries_reduced_state_rate_with_full_local_rates():
    rule = state_rules()["IL"]
    r = calc(mk("IL", [("COFF-2", 2, None)]))
    ln = r["lines"][0]
    assert ln["rule_code"] == "GROC.REDUCED"
    expected = 4_800 * (Fraction(rule.grocery_state_ppm, 1_000_000) + combined(("IL-C1", "IL-CITY1", "IL-TD")))
    assert r["tax"] == cents(expected)
    state_row = next(j for j in ln["jurisdictions"] if j["kind"] == "state")
    assert state_row["rate_ppm"] == rule.grocery_state_ppm


def test_groceries_fully_taxable_state():
    r = calc(mk("MS", ["GRAN-04"], zk=4))
    assert r["lines"][0]["rule_code"] == "GROC.TAXABLE" and r["tax"] > 0


def test_digital_downloads_taxable_and_exempt():
    assert calc(mk("CA", ["EBOOK-01"]))["lines"][0]["rule_code"] == "DIGITAL.EXEMPT"
    r = calc(mk("ME", ["EBOOK-01"]))
    assert r["lines"][0]["rule_code"] == "DIGITAL.TAXABLE" and r["tax"] == cents(1_299 * rate("ME"))


def test_saas_partial_base():
    r = calc(mk("TX", ["SAAS-STOR"], ship_from="TX-DAL"))
    ln = r["lines"][0]
    assert ln["rule_code"] == "SAAS.PARTIAL" and ln["taxable_amount"] == 7_920
    assert r["tax"] == cents(Fraction(9_900 * 80, 100) * combined(TX1))


def test_saas_reduced_state_rate():
    r = calc(mk("CT", ["SAAS-STOR"]))
    assert r["lines"][0]["rule_code"] == "SAAS.REDUCED"
    assert r["tax"] == cents(Fraction(9_900 * state_rules()["CT"].saas_state_ppm, 1_000_000))


def test_saas_exempt_and_taxable():
    assert calc(mk("MN", ["SAAS-STOR"]))["tax"] == 0
    assert calc(mk("NY", ["SAAS-STOR"], zk=3))["lines"][0]["rule_code"] == "SAAS.TAXABLE"


def test_installation_taxable_or_exempt():
    assert calc(mk("WA", ["SVC-MOUNT"]))["lines"][0]["rule_code"] == "INSTALL.TAXABLE"
    r = calc(mk("GA", ["SVC-MOUNT"]))
    assert r["lines"][0]["rule_code"] == "INSTALL.EXEMPT" and r["tax"] == 0


def test_rate_change_effective_date():
    from datetime import date
    before = calc(mk("NC", ["DESK-01"], on=date(2025, 12, 31)))
    after = calc(mk("NC", ["DESK-01"], on=date(2026, 1, 1)))
    assert after["tax"] > before["tax"]
    assert before["rule_versions"]["rates"]["NC"] == "2018-01-01" and after["rule_versions"]["rates"]["NC"] == "2026-01-01"


def test_every_line_has_an_explanation():
    r = calc(mk("NY", ["COAT-01", "TEE-01", "SAAS-STOR", "RX-REFILL", "COFF-2"], shipping=699))
    for ln in r["lines"]:
        assert ln["rule_code"] and ln["rule"]
        assert sum(j["tax"] for j in ln["jurisdictions"]) == ln["tax"]
