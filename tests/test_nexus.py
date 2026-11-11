from datetime import date

from taxengine.nexus import alerts, registration_date, registrations, track
from taxengine.sim import SIM_END, SIM_START

START = date(2025, 1, 1)


def series(days, per_day_sales=0, per_day_txns=0, mp_share=0.0, start_day=0):
    rows = [[0, 0, 0, 0] for _ in range(days)]
    for i in range(start_day, days):
        rows[i] = [per_day_sales, per_day_txns, int(per_day_sales * mp_share), int(per_day_txns * mp_share)]
    return rows


def test_registration_date_rules():
    assert registration_date(date(2026, 3, 12), "next_month") == date(2026, 4, 1)
    assert registration_date(date(2026, 12, 3), "next_month") == date(2027, 1, 1)
    assert registration_date(date(2026, 3, 12), "30_days") == date(2026, 4, 11)
    assert registration_date(date(2026, 7, 3), "next_quarter") == date(2026, 10, 1)
    assert registration_date(date(2026, 10, 31), "next_quarter") == date(2027, 1, 1)


def test_crossing_by_sales_on_exact_day():
    # KS: $100,000, no transaction test. $1,000 a day crosses on day 100 (index 99).
    ns = track({"KS": series(200, 100_000)}, START)["KS"]
    assert ns.crossed_on == date(2025, 4, 10) and ns.crossed_by == "sales"
    assert ns.registration_date == date(2025, 5, 1)


def test_crossing_by_transactions():
    # OH: $100,000 or 200 transactions. 5 small orders a day crosses on day 40.
    ns = track({"OH": series(100, 1_000, 5)}, START)["OH"]
    assert ns.crossed_by == "transactions" and ns.crossed_on == date(2025, 2, 9)


def test_and_threshold_needs_both():
    # NY: $500,000 and 100 transactions. Many orders but small sales: never crosses.
    ns = track({"NY": series(365, 100_000, 50)}, START)["NY"]
    assert ns.crossed_on is None
    ns = track({"NY": series(365, 600_000, 1)}, START)["NY"]
    assert ns.crossed_on == date(2025, 4, 10) and ns.crossed_by == "sales and transactions"


def test_marketplace_sales_excluded_where_state_says_so():
    # CA: $500,000, marketplace sales do not count. Half the volume is marketplace.
    with_mp = track({"CA": series(365, 400_000, 0, mp_share=0.5)}, START)["CA"]
    assert with_mp.crossed_on == date(2025, 9, 7)  # 250 days x $2,000 net
    # FL counts marketplace sales: same volume crosses on day 100 at $1,000
    fl = track({"FL": series(365, 100_000, 0, mp_share=0.5)}, START)["FL"]
    assert fl.crossed_on == date(2025, 4, 10)


def test_trailing_window_drops_old_sales():
    rows = series(800, 0)
    for i in range(0, 50):
        rows[i] = [150_000, 1, 0, 0]  # $75,000 in the first 50 days
    for i in range(400, 450):
        rows[i] = [150_000, 1, 0, 0]  # another $75,000 more than a year later
    ns = track({"KS": rows}, START)["KS"]
    assert ns.crossed_on is None  # the window never holds both bursts
    assert ns.approached_on is None or ns.trailing_sales < 10_000_000


def test_approaching_alert_before_crossing():
    ns = track({"KS": series(200, 100_000)}, START)["KS"]
    assert ns.approached_on == date(2025, 3, 21)  # day 80 = 80% of $100,000
    a = alerts({"KS": ns}, START)
    assert [x["kind"] for x in a] == ["approaching", "crossed"]


def test_physical_nexus_from_warehouse():
    ns = track({"PA": series(30, 0)}, START)["PA"]
    assert ns.crossed_on is None and ns.collection_start == date(2026, 5, 1) and ns.reason.startswith("physical")
    assert registrations({"PA": ns}) == {"PA": date(2026, 5, 1)}


def test_no_sales_tax_state_tracked_but_not_registered():
    ns = track({"OR": series(200, 100_000, 5)}, START)["OR"]
    assert ns.crossed_on is not None and ns.collection_start is None and ns.status(date(2025, 12, 1)) == "no_sales_tax"


def test_simulation_has_states_crossing_mid_year(sim):
    crossed = [ns for ns in sim.nexus.values() if ns.has_tax and ns.crossed_on and SIM_START <= ns.crossed_on <= SIM_END]
    assert len(crossed) >= 5
    for ns in crossed:
        assert ns.collection_start > ns.crossed_on
    statuses = {ns.status(SIM_END) for ns in sim.nexus.values()}
    assert {"registered", "no_sales_tax"} <= statuses
