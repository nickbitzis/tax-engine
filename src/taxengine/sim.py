"""Seeded simulation: 12 months of orders (Nov 2025 to Oct 2026), about 200,000 at full scale.

The retailer grows over the year and launches marketing in 12 new states on
fixed dates, so several states cross their nexus thresholds mid-year. The year
before the simulation is generated as daily totals only, so the trailing 12
month window is full on day one.

Pipeline: generate orders -> daily totals per state -> nexus tracker ->
registrations -> calculate every order with the rate table as it was loaded
that day -> transaction log and return buckets.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

from taxengine.calc import Address, Cart, Context, LineIn, calculate
from taxengine.catalog import PRODUCTS
from taxengine.certs import Certificate, seed_certificates
from taxengine.lookup import zip_table
from taxengine.nexus import StateNexus, registrations, track
from taxengine.rules import NO_SALES_TAX, STATES

DEFAULT_SEED = 20261
SIM_START = date(2025, 11, 1)
SIM_END = date(2026, 10, 31)
HISTORY_START = SIM_START - timedelta(days=365)
DAYS = (SIM_END - SIM_START).days + 1

LAUNCHES = {"CO": date(2026, 1, 15), "MN": date(2026, 2, 1), "TN": date(2026, 3, 1), "MO": date(2026, 3, 15), "SC": date(2026, 4, 1),
            "AL": date(2026, 5, 1), "LA": date(2026, 5, 15), "KY": date(2026, 6, 1), "OK": date(2026, 7, 1), "IA": date(2026, 7, 15),
            "NV": date(2026, 8, 1), "UT": date(2026, 8, 15)}
PRE_LAUNCH = 0.03
SEASON = {11: 1.25, 12: 1.45, 1: 0.85, 2: 0.85, 3: 0.95, 4: 1.0, 5: 1.0, 6: 1.0, 7: 1.05, 8: 1.15, 9: 1.0, 10: 1.05}
STREETS = ["Main St", "Harbor Rd", "Oak Ave", "Mill St", "Cedar Ln", "Ridge Dr"]
CODE_WEIGHTS = {"GEN": 40, "CLOTH": 26, "GROC": 8, "RX": 4, "DIGITAL": 10, "SAAS": 7, "INSTALL": 3}
MARKETPLACE_SHARE = 0.12
B2B_SHARE = 0.03
REFUND_RATE = 0.05


def months() -> list[str]:
    out, d = [], SIM_START
    while d <= SIM_END:
        out.append(f"{d.year}-{d.month:02d}")
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


@dataclass
class Order:
    cart: Cart
    refund_on: date | None = None
    refund_line: int | None = None  # None = full refund


@dataclass
class Bucket:
    """One state and month on the seller's return."""
    orders: int = 0
    gross: int = 0
    taxable: int = 0  # state level taxable sales, cents
    collected: int = 0
    refunds: int = 0  # count
    refund_gross: int = 0
    refund_tax: int = 0
    jur: dict[str, list[int]] = field(default_factory=dict)  # id -> [base_x100, collected cents, exact applied units, exact due units]


@dataclass
class SimResult:
    seed: int
    scale: float
    orders: int
    log: list[tuple]  # (order id, date, state, channel, customer, collector, subtotal, tax, kind)
    buckets: dict[tuple[str, str], Bucket]
    nexus: dict[str, StateNexus]
    certificates: list[Certificate]
    by_collector: dict[str, list[int]]  # collector -> [orders, subtotal, tax]
    seconds: float
    examples: dict[str, str] = field(default_factory=dict)

    @property
    def registrations(self) -> dict[str, date]:
        return registrations(self.nexus)


def _state_weights(on: date) -> tuple[list[str], list[float]]:
    sts = list(STATES)
    w = []
    for s in sts:
        base = STATES[s][2]
        if s in LAUNCHES and on < LAUNCHES[s]:
            base *= PRE_LAUNCH
        w.append(base)
    return sts, w


def _pick_products(rng: random.Random) -> list:
    by_code: dict[str, list] = {}
    for p in PRODUCTS.values():
        by_code.setdefault(p.tax_code, []).append(p)
    return by_code


def generate_orders(seed: int = DEFAULT_SEED, scale: float = 1.0) -> tuple[list[Order], dict[str, list[list[int]]], list[Certificate]]:
    rng = random.Random(seed)
    certs = seed_certificates(seed)
    by_code = _pick_products(rng)
    codes, cw = list(CODE_WEIGHTS), list(CODE_WEIGHTS.values())
    pw = {p.sku: 1 / (p.price ** 0.75) for p in PRODUCTS.values()}  # cheaper items sell more often
    zips_by_state: dict[str, list[str]] = {}
    for z in zip_table():
        zips_by_state.setdefault(next(s for s, v in STATES.items() if z.startswith(v[1])), []).append(z)
    hist_days = (SIM_START - HISTORY_START).days
    daily = {s: [[0, 0, 0, 0] for _ in range(hist_days + DAYS)] for s in STATES}

    # Prior year: daily totals only, at about half the starting volume, launch states at the pre-launch level.
    sts, w0 = _state_weights(HISTORY_START)
    tot = sum(w0)
    for i in range(hist_days):
        d = HISTORY_START + timedelta(days=i)
        n = 160 * scale * SEASON[d.month] * (0.8 + 0.2 * i / hist_days)
        for s, wt in zip(sts, w0, strict=True):
            mean = n * wt / tot
            k = int(mean) + (1 if rng.random() < mean - int(mean) else 0)
            if k:
                sales = int(k * rng.gauss(15_000, 1_800))
                mp = int(k * MARKETPLACE_SHARE + rng.random())
                daily[s][i][0] += sales
                daily[s][i][1] += k
                daily[s][i][2] += int(sales * mp / k)
                daily[s][i][3] += mp

    orders: list[Order] = []
    certs_by_customer = {c.customer_id: c for c in certs}
    b2b_ids = list(certs_by_customer)
    seq = 0
    for di in range(DAYS):
        d = SIM_START + timedelta(days=di)
        sts, w = _state_weights(d)
        n_mean = 370 * scale * (1 + 0.9 * di / (DAYS - 1)) * SEASON[d.month]
        n = max(0, int(rng.gauss(n_mean, n_mean ** 0.5)))
        dests = rng.choices(sts, w, k=n)
        for st in dests:
            seq += 1
            channel = "marketplace" if rng.random() < MARKETPLACE_SHARE else "direct"
            customer = f"C{rng.randrange(1, 60_001):05d}"
            qty_mult = 1
            if channel == "direct" and rng.random() < B2B_SHARE:
                customer = rng.choice(b2b_ids)
                cert = certs_by_customer[customer]
                if rng.random() < 0.8:
                    st = rng.choice(cert.states)
                qty_mult = rng.choice([2, 3, 4])
            nlines = rng.choices([1, 2, 3, 4], [45, 30, 17, 8])[0]
            lines = []
            for _ in range(nlines):
                pool = by_code[rng.choices(codes, cw)[0]]
                p = rng.choices(pool, [pw[x.sku] for x in pool])[0]
                price = None
                r = rng.random()
                if r < 0.10:
                    price = p.price * 90 // 100
                elif r < 0.15:
                    price = p.price * 80 // 100
                q = rng.choices([1, 1, 1, 2, 3])[0] * (qty_mult if p.tax_code not in ("SAAS", "DIGITAL") else 1)
                lines.append(LineIn(p.sku, q, price))
            physical = any(PRODUCTS[ln.sku].tax_code not in ("DIGITAL", "SAAS", "INSTALL") for ln in lines)
            items = sum((ln.price if ln.price is not None else PRODUCTS[ln.sku].price) * ln.qty for ln in lines)
            shipping = (0 if items >= 7_500 else 699) if physical else 0
            handling = 199 if physical and rng.random() < 0.2 else 0
            z = rng.choice(zips_by_state[st])
            street = f"{rng.randrange(1, 2000)} {rng.choice(STREETS)}"
            cart = Cart(f"O{seq:07d}", d, Address(street, st, z), lines, shipping, handling, customer, channel)
            o = Order(cart)
            if rng.random() < REFUND_RATE:
                o.refund_on = d + timedelta(days=rng.randrange(3, 46))
                o.refund_line = None if (len(lines) == 1 or rng.random() < 0.6) else rng.randrange(len(lines))
            orders.append(o)
            row = daily[st][hist_days + di]
            total = items + shipping + handling
            row[0] += total
            row[1] += 1
            if channel == "marketplace":
                row[2] += total
                row[3] += 1
    return orders, daily, certs


def _month(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def run(seed: int = DEFAULT_SEED, scale: float = 1.0) -> SimResult:
    t0 = time.perf_counter()
    orders, daily, certs = generate_orders(seed, scale)
    tracked = track(daily, HISTORY_START, SIM_END)
    ctx = Context(registrations=registrations(tracked), certificates={}, as_loaded=True)
    for c in certs:
        ctx.certificates.setdefault(c.customer_id, []).append(c)
    buckets: dict[tuple[str, str], Bucket] = {}
    log: list[tuple] = []
    by_collector: dict[str, list[int]] = {}
    for o in orders:
        c = o.cart
        need_lines = o.refund_on is not None and o.refund_on <= SIM_END
        r = calculate(c, ctx, explain=need_lines)
        log.append((c.id, c.date, c.ship_to.state, c.channel, c.customer_id, r["collector"], r["subtotal"], r["tax"], "sale"))
        agg = by_collector.setdefault(r["collector_code"], [0, 0, 0])
        agg[0] += 1
        agg[1] += r["subtotal"]
        agg[2] += r["tax"]
        if r["collector"] != "seller":
            continue
        b = buckets.setdefault((c.ship_to.state, _month(c.date)), Bucket())
        b.orders += 1
        b.gross += r["subtotal"]
        for j in r["jurisdictions"]:
            row = b.jur.setdefault(j["id"], [0, 0, 0, 0])
            row[0] += j["base_x100"]
            row[1] += j["tax"]
            row[2] += j["exact_units"]
            row[3] += j["due_units"]
            if j["kind"] == "state":
                b.taxable += j["base_x100"] // 100
        b.collected += r["tax"]
        if need_lines:
            _refund(o, r, buckets, log)
    return SimResult(seed, scale, len(orders), log, buckets, tracked, certs, by_collector, time.perf_counter() - t0)


def _refund(o: Order, r: dict, buckets: dict, log: list) -> None:
    """Refunds are netted in the month of the refund, using the tax actually collected on the refunded lines."""
    lines = r["lines"] if o.refund_line is None else [r["lines"][o.refund_line]]
    gross = sum(ln["amount"] for ln in lines)
    tax = sum(ln["tax"] for ln in lines)
    b = buckets.setdefault((o.cart.ship_to.state, _month(o.refund_on)), Bucket())
    b.refunds += 1
    b.refund_gross += gross
    b.refund_tax += tax
    rates = {j["id"]: j for j in r["jurisdictions"]}
    for ln in lines:
        for lj in ln["jurisdictions"]:
            row = b.jur.setdefault(lj["id"], [0, 0, 0, 0])
            base = ln["taxable_amount"] * 100 if lj["applies"] else 0
            row[0] -= base
            row[1] -= lj["tax"]
            row[2] -= base * lj["rate_ppm"]
            due_ppm = lj["rate_ppm"] if rates[lj["id"]]["exact_units"] == rates[lj["id"]]["due_units"] else _due_ppm(lj, rates[lj["id"]])
            row[3] -= base * due_ppm
    log.append((o.cart.id + "-R", o.refund_on, o.cart.ship_to.state, o.cart.channel, o.cart.customer_id, "seller", -gross, -tax, "refund"))


def _due_ppm(lj: dict, j: dict) -> int:
    return j["due_units"] // j["base_x100"] if j["base_x100"] else lj["rate_ppm"]


def taxing_states() -> list[str]:
    return [s for s in STATES if s not in NO_SALES_TAX]


def _code_version() -> str:
    import hashlib
    from pathlib import Path

    from taxengine.rules import fingerprint

    h = hashlib.sha256(fingerprint().encode())
    here = Path(__file__).parent
    for name in ("calc.py", "sim.py", "certs.py", "nexus.py", "lookup.py", "catalog.py"):
        h.update((here / name).read_bytes())
    return h.hexdigest()[:12]


def load_or_run(seed: int = DEFAULT_SEED, scale: float = 1.0, cache_dir: str | None = None) -> SimResult:
    """Run the simulation, or load it from a local pickle cache keyed by seed, scale, rules and code.

    The cache is a file this process wrote itself; it is never shipped or downloaded.
    """
    import pickle
    from pathlib import Path

    if not cache_dir:
        return run(seed, scale)
    path = Path(cache_dir) / f"sim-{seed}-{scale:g}-{_code_version()}.pkl"
    if path.exists():
        with path.open("rb") as f:
            return pickle.load(f)
    res = run(seed, scale)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("wb") as f:
        pickle.dump(res, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(path)
    return res
