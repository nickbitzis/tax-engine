from fractions import Fraction

from helpers import calc, cents, combined, mk, rate

from taxengine.calc import D, allocate, half_up


def test_shipping_taxable():
    r = calc(mk("NJ", ["SKIL-01"], shipping=699))
    ship = r["lines"][-1]
    assert ship["rule_code"] == "SHIPPING.TAXABLE" and ship["tax"] == cents(699 * rate("NJ"))


def test_shipping_exempt():
    r = calc(mk("MA", ["SKIL-01"], shipping=699))
    assert r["lines"][-1]["rule_code"] == "SHIPPING.EXEMPT" and r["lines"][-1]["tax"] == 0


def test_shipping_proportional_to_taxable_items():
    r = calc(mk("MI", ["SKIL-01", "RX-REFILL"], shipping=699))
    ship = r["lines"][-1]
    assert ship["rule_code"] == "SHIPPING.PROPORTIONAL"
    base = cents(Fraction(699 * 4_500, 8_000))
    assert ship["taxable_amount"] == base
    assert ship["tax"] == cents(base * rate("MI"))


def test_shipping_proportional_all_exempt_items_is_zero():
    r = calc(mk("MI", ["RX-REFILL"], shipping=699))
    assert r["lines"][-1]["taxable_amount"] == 0 and r["tax"] == 0


def test_handling_taxable_when_shipping_exempt():
    r = calc(mk("ME", ["SKIL-01"], shipping=699, handling=199))
    codes = [ln["rule_code"] for ln in r["lines"]]
    assert codes == ["GEN.TAXABLE", "SHIPPING.EXEMPT", "HANDLING.TAXABLE"]


def test_handling_follows_shipping_by_default():
    r = calc(mk("NJ", ["SKIL-01"], shipping=699, handling=199))
    assert r["lines"][-1]["rule_code"] == "HANDLING.TAXABLE"
    r = calc(mk("MA", ["SKIL-01"], shipping=699, handling=199))
    assert r["lines"][-1]["rule_code"] == "HANDLING.EXEMPT"


def test_half_up():
    assert half_up(D // 2) == 1
    assert half_up(D // 2 - 1) == 0
    assert half_up(-D // 2) == -1
    assert half_up(250, 100) == 3


def test_allocate_sums_to_total_and_favours_largest_remainder():
    exact = [D * 1 + D // 3, D * 2 + D * 2 // 3, D // 2]
    total = half_up(sum(exact))  # 4.49999999 cents -> 4
    assert total == 4
    assert allocate(exact, total) == [1, 3, 0]  # the one spare cent goes to the largest remainder (0.667)
    assert allocate(exact, 5) == [1, 3, 1]


def test_line_vs_invoice_rounding_differ_on_many_small_lines():
    # MI has no local tax. Ten lines of $0.09: each line is 0.585 cents and rounds up to 1 cent.
    lines = [("PENS-12", 1, 9)] * 10
    line_mode = calc(mk("MI", lines))  # MI rounds per line
    exact = Fraction(90) * rate("MI")
    assert line_mode["rounding"] == "line"
    assert line_mode["tax"] == 10 * cents(9 * rate("MI"))  # 10 x 1 cent
    assert cents(exact) == 6  # invoice level would be 5.85 -> 6 cents
    assert line_mode["tax"] != cents(exact)


def test_invoice_rounding_matches_exact_total():
    lines = [("PENS-12", 1, 9)] * 10
    r = calc(mk("MD", lines))  # MD rounds at invoice level, no local tax
    assert r["rounding"] == "invoice"
    assert r["tax"] == cents(90 * rate("MD"))
    assert sum(ln["tax"] for ln in r["lines"]) == r["tax"]


def test_jurisdiction_amounts_sum_to_total_in_both_modes():
    for st in ("TX", "CA"):
        r = calc(mk(st, ["PENS-12", "NOTE-03", "FLSH-01", "SKIL-01", ("LAMP-01", 1, 3_333)], zk=4))
        assert sum(j["tax"] for j in r["jurisdictions"]) == r["tax"]
        assert r["tax"] == sum(ln["tax"] for ln in r["lines"])


def test_invoice_total_within_half_cent_of_exact():
    r = calc(mk("CA", ["PENS-12", "NOTE-03", "FLSH-01", "SKIL-01", ("LAMP-01", 1, 3_333)], zk=4))
    exact = sum(Fraction(j["exact_units"], D) for j in r["jurisdictions"])
    assert abs(r["tax"] - exact) <= Fraction(1, 2)
    assert combined(("CA", "CA-C2", "CA-CITY3", "CA-SD")) > 0
