import csv
import io

import pytest

from taxengine import returns
from taxengine.calc import half_up
from taxengine.sim import Bucket, months


def test_periods_by_filing_frequency():
    assert returns.periods("TX")[0] == "2025-11" and len(returns.periods("TX")) == 12
    assert returns.periods("KS") == ["2025-Q4", "2026-Q1", "2026-Q2", "2026-Q3", "2026-Q4"]
    assert returns.months_in("2026-Q3") == ["2026-07", "2026-08", "2026-09"]
    assert returns.due_date("2026-Q3").isoformat() == "2026-10-20"


def test_unknown_period_is_an_error(sim):
    with pytest.raises(returns.PeriodError):
        returns.state_return(sim, "KS", "2026-03")


def test_refunds_are_netted_in_the_refund_month():
    b1 = Bucket(orders=2, gross=20_000, taxable=20_000, collected=1_200, jur={"MI": [2_000_000, 1_200, 120_000_000_000, 120_000_000_000]})
    b2 = Bucket(refunds=1, refund_gross=10_000, refund_tax=650, jur={"MI": [-1_000_000, -650, -65_000_000_000, -65_000_000_000]})

    class R:
        buckets = {("MI", "2026-03"): b1, ("MI", "2026-04"): b2}

    r = returns.state_return(R, "MI", "2026-Q1")
    assert r["net_tax_collected"] == 1_200 and r["refunds"] == 0
    r = returns.state_return(R, "MI", "2026-Q2")
    assert r["refunds"] == 1 and r["net_tax_collected"] == -650 and r["net_sales"] == -10_000
    assert r["jurisdictions"][0]["collected"] == -650 and r["jurisdictions"][0]["due"] == -650


def test_return_totals_match_transaction_log(sim):
    # every seller-collected sale and every refund in the log appears on exactly one return
    collected = sum(row[7] for row in sim.log if row[5] == "seller")
    rows = returns.summary(sim)
    assert sum(r["net_tax_collected"] for r in rows) == collected


def test_jurisdiction_rows_sum_to_state_totals(sim):
    for st, p in (("TX", "2026-03"), ("CA", "2026-08"), ("KS", "2026-Q3")):
        r = returns.state_return(sim, st, p)
        assert sum(j["collected"] for j in r["jurisdictions"]) == r["net_tax_collected"]
        assert sum(j["due"] for j in r["jurisdictions"]) == r["tax_due"]


def test_reconciliation_splits_difference_into_lag_and_rounding(sim):
    rec = returns.reconcile(sim)
    assert rec["difference"] == rec["tax_due"] - rec["net_tax_collected"]
    assert rec["difference"] == rec["rate_lag"] + rec["rounding"]
    lag_states = {r["state"] for r in returns.summary(sim) if r["rate_lag"]}
    assert lag_states <= {"KS"}


def test_rate_lag_only_on_the_late_loaded_jurisdiction(sim):
    for p in ("2026-Q3",):
        r = returns.state_return(sim, "KS", p)
        for j in r["jurisdictions"]:
            assert j["rate_lag"] >= 0
            if j["id"] != "KS-CITY1":
                assert j["rate_lag"] == 0
    for st, p in (("TX", "2026-07"), ("CA", "2026-07")):
        assert returns.state_return(sim, st, p)["rate_lag"] == 0


def test_due_is_rounded_once_per_jurisdiction():
    units = [33_333_333, 33_333_333, 33_333_333]  # three invoices of 0.333 cents
    assert sum(half_up(u) for u in units) == 0
    assert half_up(sum(units)) == 1


def test_export_csv_has_summary_and_jurisdiction_rows(sim):
    r = returns.state_return(sim, "TX", "2026-03")
    rows = list(csv.reader(io.StringIO(returns.export_csv(r))))
    assert rows[1][:3] == ["state", "period", "frequency"] and rows[2][0] == "TX"
    assert rows[4][0] == "jurisdiction_id"
    assert {x[0] for x in rows[5:]} == {j["id"] for j in r["jurisdictions"]}
    assert f"{r['net_tax_collected'] / 100:.2f}" == rows[2][11]


def test_partial_periods_are_flagged(sim):
    assert not returns.state_return(sim, "KS", "2025-Q4")["complete"]
    assert returns.state_return(sim, "KS", "2026-Q1")["complete"]
    assert months()[0] == "2025-11" and len(months()) == 12


def test_unregistered_and_marketplace_sales_are_not_on_returns(sim):
    seller_orders = sum(1 for row in sim.log if row[8] == "sale" and row[5] == "seller")
    assert sum(b.orders for b in sim.buckets.values()) == seller_orders
    assert seller_orders < sum(1 for row in sim.log if row[8] == "sale")
