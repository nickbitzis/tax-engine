import json

from taxengine import golden
from taxengine.sim import SIM_END, SIM_START, run


def test_simulation_is_deterministic():
    a, b = run(scale=0.02), run(scale=0.02)
    assert a.orders == b.orders and a.log[:50] == b.log[:50] and a.by_collector == b.by_collector


def test_simulation_covers_the_year_and_all_order_kinds(sim):
    dates = [row[1] for row in sim.log if row[8] == "sale"]
    assert min(dates) == SIM_START and max(dates) == SIM_END
    codes = set(sim.by_collector)
    assert {"COLLECT.SELLER", "COLLECT.MARKETPLACE", "COLLECT.NOT_REGISTERED", "COLLECT.NO_SALES_TAX"} <= codes
    assert any(row[8] == "refund" for row in sim.log)
    assert any(row[4].startswith("B") for row in sim.log)


def test_orders_grow_over_the_year(sim):
    first = sum(1 for row in sim.log if row[8] == "sale" and row[1].month == 1)
    last = sum(1 for row in sim.log if row[8] == "sale" and row[1].month == 9)
    assert last > first * 1.4


def test_golden_file_matches(tmp_path):
    ok, problems, n = golden.gate("golden/carts.json")
    assert ok, problems
    assert n >= 50


def test_golden_gate_fails_on_a_one_cent_change(tmp_path):
    data = json.loads(open("golden/carts.json").read())
    data["carts"][1]["expected"]["tax"] += 1
    p = tmp_path / "g.json"
    p.write_text(json.dumps(data))
    ok, problems, _ = golden.gate(p)
    assert not ok and problems[0].startswith("G02")


def test_golden_carts_cover_every_rule_family():
    rules = {code.split(".")[0] for c in golden.build()["carts"] for code in c["expected"]["rules"]}
    assert {"GEN", "CLOTH", "GROC", "RX", "DIGITAL", "SAAS", "INSTALL", "SHIPPING", "HANDLING", "HOLIDAY", "CERT", "COLLECT"} <= rules
