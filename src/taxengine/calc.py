"""Tax calculation for one cart, with an explanation of every component.

Order of decisions, each recorded in the explanation:
  1. Sourcing: destination (ship-to address) or origin (warehouse address, only
     for sales shipped from inside an origin state).
  2. Who collects: nobody (no state tax), the marketplace (facilitator law in
     effect), nobody (seller not registered yet), or the seller.
  3. Exemption certificate for the customer, if any.
  4. Per line: tax holiday, then the product taxability rule for the state.
  5. Shipping and handling by the state's rule.
  6. Rates per jurisdiction on the order date, exact integer tax, then rounding
     at line or invoice level, allocated back to jurisdictions so they sum.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from taxengine.catalog import PRODUCTS
from taxengine.certs import Certificate, check
from taxengine.lookup import resolve_cached
from taxengine.rules import RULESET_VERSION, StateRule, fingerprint, fmt_rate, jurisdictions, state_rules

D = 100 * 1_000_000  # exact tax unit: cents x pct(100) x rate ppm(1e6)


@dataclass(frozen=True, slots=True)
class Address:
    street: str
    state: str
    zip: str


@dataclass(frozen=True, slots=True)
class Warehouse:
    id: str
    address: Address
    opened: date


WAREHOUSES = {
    "TX-DAL": Warehouse("TX-DAL", Address("4100 Commerce Dr", "TX", "75001"), date(2018, 1, 1)),
    "PA-ALN": Warehouse("PA-ALN", Address("88 Logistics Way", "PA", "15004"), date(2026, 5, 1)),
}
NORTHEAST = {"PA", "NY", "NJ", "CT", "MA", "RI", "VT", "NH", "ME", "DE", "MD", "DC", "VA", "WV", "OH"}


def pick_warehouse(state: str, on: date) -> Warehouse:
    pa = WAREHOUSES["PA-ALN"]
    return pa if state in NORTHEAST and on >= pa.opened else WAREHOUSES["TX-DAL"]


@dataclass(slots=True)
class LineIn:
    sku: str
    qty: int = 1
    price: int | None = None  # unit price in cents; defaults to the catalog price
    id: str | None = None


@dataclass(slots=True)
class Cart:
    id: str
    date: date
    ship_to: Address
    lines: list[LineIn]
    shipping: int = 0
    handling: int = 0
    customer_id: str | None = None
    channel: str = "direct"  # direct | marketplace
    ship_from: str | None = None


@dataclass(slots=True)
class Context:
    registrations: dict[str, date] | None = None  # state -> collection start date; None means registered everywhere
    certificates: dict[str, list[Certificate]] = field(default_factory=dict)
    as_loaded: bool = False  # True: use the rate table as it was loaded on the order date (what production charged)


def half_up(units: int, d: int = D) -> int:
    if units >= 0:
        return (units + d // 2) // d
    return -((-units + d // 2) // d)


def allocate(exact: list[int], total: int) -> list[int]:
    """Split a rounded total across cells by largest remainder, so the parts sum to the total exactly."""
    floors = [e // D for e in exact]
    k = total - sum(floors)
    order = sorted(range(len(exact)), key=lambda i: (-(exact[i] % D), i))
    for i in order[:max(k, 0)]:
        floors[i] += 1
    return floors


def _taxability(rule: StateRule, code: str, unit: int, qty: int, holiday_cat: str | None, on: date):
    """Return (base_pct_of_amount or explicit base cents, rule_code, text, state_ppm_override, locals_apply, state_applies)."""
    amount = unit * qty
    for h in rule.holidays:
        if holiday_cat and h.start <= on <= h.end and holiday_cat in h.caps:
            cap = h.caps[holiday_cat]
            if cap is None or unit <= cap:
                cap_txt = "no item cap" if cap is None else f"item price ${unit / 100:,.2f} is at or under the ${cap / 100:,.2f} cap"
                if h.local_too:
                    return 0, "HOLIDAY.EXEMPT", f"{h.name} {h.start} to {h.end}: {holiday_cat} exempt, {cap_txt}", None, False, False
                return amount * 100, "HOLIDAY.STATE_ONLY", (f"{h.name} {h.start} to {h.end}: state portion exempt, {cap_txt}; "
                                                             "local taxes still apply"), None, True, False
    if code == "RX":
        return 0, "RX.EXEMPT", "prescription drugs are exempt", None, False, False
    if code == "GEN":
        return amount * 100, "GEN.TAXABLE", "general merchandise is taxable", None, True, True
    if code == "CLOTH":
        if rule.clothing == "exempt":
            return 0, "CLOTH.EXEMPT", "clothing is exempt in this state", None, False, False
        if rule.clothing == "below_threshold_exempt":
            t = rule.clothing_threshold
            if unit < t:
                return 0, "CLOTH.UNDER_THRESHOLD", f"clothing under ${t / 100:,.2f} per item is exempt (item ${unit / 100:,.2f})", None, False, False
            return amount * 100, "CLOTH.OVER_THRESHOLD", (f"clothing at or over ${t / 100:,.2f} per item is fully taxable "
                                                          f"(item ${unit / 100:,.2f})"), None, True, True
        if rule.clothing == "excess_over_threshold":
            t = rule.clothing_threshold
            excess = max(0, unit - t) * qty
            if excess == 0:
                return 0, "CLOTH.UNDER_THRESHOLD", f"clothing is exempt up to ${t / 100:,.2f} per item (item ${unit / 100:,.2f})", None, False, False
            return excess * 100, "CLOTH.EXCESS_TAXABLE", (f"only the part of each item over ${t / 100:,.2f} is taxable: "
                                                          f"(${unit / 100:,.2f} - ${t / 100:,.2f}) x {qty}"), None, True, True
        return amount * 100, "CLOTH.TAXABLE", "clothing is taxable", None, True, True
    if code == "GROC":
        if rule.groceries == "exempt":
            return 0, "GROC.EXEMPT", "groceries are exempt", None, False, False
        if rule.groceries == "reduced":
            return amount * 100, "GROC.REDUCED", (f"groceries taxed at a reduced state rate of {fmt_rate(rule.grocery_state_ppm)}; "
                                                  "local rates apply in full"), rule.grocery_state_ppm, True, True
        return amount * 100, "GROC.TAXABLE", "groceries are taxable at the full rate", None, True, True
    if code == "DIGITAL":
        if rule.digital == "exempt":
            return 0, "DIGITAL.EXEMPT", "digital downloads are exempt", None, False, False
        return amount * 100, "DIGITAL.TAXABLE", "digital downloads are taxable", None, True, True
    if code == "SAAS":
        if rule.saas == "taxable":
            return amount * 100, "SAAS.TAXABLE", "SaaS is taxable", None, True, True
        if rule.saas == "partial":
            p = rule.saas_base_pct
            return amount * p, "SAAS.PARTIAL", f"SaaS is taxed on {p}% of the price", None, True, True
        if rule.saas == "reduced":
            return amount * 100, "SAAS.REDUCED", f"SaaS taxed at a reduced state rate of {fmt_rate(rule.saas_state_ppm)}", rule.saas_state_ppm, True, True
        return 0, "SAAS.EXEMPT", "SaaS is not taxable", None, False, False
    if code == "INSTALL":
        if rule.install == "taxable":
            return amount * 100, "INSTALL.TAXABLE", "installation services are taxable", None, True, True
        return 0, "INSTALL.EXEMPT", "separately stated installation services are exempt", None, False, False
    raise ValueError(f"unknown tax code {code}")


class CartError(ValueError):
    pass


def calculate(cart: Cart, ctx: Context | None = None, explain: bool = True) -> dict:
    ctx = ctx or Context()
    rules = state_rules()
    st = cart.ship_to.state
    if st not in rules:
        raise CartError(f"unknown state {st}")
    rule = rules[st]
    on = cart.date
    js = jurisdictions()
    notes: list[str] = []

    # 1. sourcing
    wh = WAREHOUSES[cart.ship_from] if cart.ship_from else pick_warehouse(st, on)
    if rule.sourcing == "origin" and wh.address.state == st:
        res = resolve_cached(st, wh.address.zip, wh.address.street)
        sourcing = {"rule": "origin", "address": "ship_from", "warehouse": wh.id,
                    "why": f"{st} sources in-state sales to the seller's location, so the {wh.id} warehouse rates apply"}
    else:
        res = resolve_cached(st, cart.ship_to.zip, cart.ship_to.street)
        why = f"{st} is a destination state" if rule.sourcing == "destination" else f"{st} is an origin state, but the order ships from {wh.address.state}"
        sourcing = {"rule": "destination", "address": "ship_to", "warehouse": wh.id, "why": why + ", so ship-to address rates apply"}
    sourcing["lookup"] = res.method
    if res.override:
        sourcing["override"] = res.override.note
        sourcing["zip_only_jurisdictions"] = list(res.zip_only)
    jur = [js[j] for j in res.jurisdictions]

    # 2. who collects
    collector, collector_code = "seller", "COLLECT.SELLER"
    if not rule.has_tax:
        collector, collector_code = "none", "COLLECT.NO_SALES_TAX"
        notes.append(f"{st} has no sales tax (illustrative)")
    elif cart.channel == "marketplace" and on >= rule.mpf_effective:
        collector, collector_code = "marketplace", "COLLECT.MARKETPLACE"
        notes.append(f"sold through a marketplace: under the {st} marketplace facilitator rule (since {rule.mpf_effective}) "
                     "the marketplace collects and remits")
    elif ctx.registrations is not None and (st not in ctx.registrations or on < ctx.registrations[st]):
        collector, collector_code = "none", "COLLECT.NOT_REGISTERED"
        start = ctx.registrations.get(st)
        notes.append(f"seller not registered in {st} on {on}" + (f"; collection starts {start}" if start else "; below the economic nexus threshold"))
    elif cart.channel == "marketplace":
        notes.append(f"marketplace sale, but the {st} facilitator rule starts {rule.mpf_effective}, so the seller collects")

    # 3. certificate
    cert_result = None
    if collector == "seller" and cart.customer_id:
        certs = ctx.certificates.get(cart.customer_id, [])
        best = None
        for c in certs:
            r = check(c, st, on, rule)
            if r.applies:
                best = r
                break
            best = best or r
        cert_result = best or check(None, st, on, rule)
        if cert_result.applies:
            notes.append(cert_result.message)
        elif certs:
            notes.append(cert_result.message)
    exempt_all = cert_result is not None and cert_result.applies

    rates = [(j, j.entry_at(on, ctx.as_loaded)) for j in jur]
    correct = [j.entry_at(on) for j in jur] if ctx.as_loaded else [e for _, e in rates]
    lines_out: list[dict] = []
    cells: list[tuple[int, int, int, int, int]] = []  # (line, jurisdiction, exact units, base x100, exact units at the correct rate)
    item_total = 0
    item_taxable = 0

    def add_line(idx: int, lid: str, sku: str, name: str, code: str, qty: int, unit: int, amount: int, base_x100: int,
                 rcode: str, text: str, state_override: int | None, locals_apply: bool, state_applies: bool) -> None:
        jrows = []
        for ji, (j, e) in enumerate(rates):
            rate = e.rate_ppm if e else 0
            ce = correct[ji]
            crate = ce.rate_ppm if ce else 0
            applies = state_applies if j.kind == "state" else locals_apply
            if j.kind == "state" and state_override is not None and applies:
                rate = crate = state_override
            b = base_x100 if applies else 0
            cells.append((idx, ji, b * rate, b, b * crate))
            if explain:
                jrows.append({"id": j.id, "name": j.name, "kind": j.kind, "rate": fmt_rate(rate) if applies else "0%",
                              "rate_ppm": rate if applies else 0, "rate_effective": e.effective_from.isoformat() if e else None,
                              "applies": applies, "tax": 0})
        lines_out.append({"id": lid, "sku": sku, "name": name, "tax_code": code, "qty": qty, "unit_price": unit, "amount": amount,
                          "taxable_amount": half_up(base_x100, 100), "rule_code": rcode, "rule": text, "jurisdictions": jrows, "tax": 0})

    for i, ln in enumerate(cart.lines):
        p = PRODUCTS.get(ln.sku)
        if p is None:
            raise CartError(f"unknown sku {ln.sku}")
        if ln.qty < 1:
            raise CartError("qty must be at least 1")
        unit = p.price if ln.price is None else ln.price
        amount = unit * ln.qty
        item_total += amount
        lid = ln.id or f"L{i + 1}"
        if collector != "seller":
            add_line(i, lid, p.sku, p.name, p.tax_code, ln.qty, unit, amount, 0, collector_code, notes[0] if notes else "", None, False, False)
            continue
        if exempt_all:
            add_line(i, lid, p.sku, p.name, p.tax_code, ln.qty, unit, amount, 0, cert_result.code, cert_result.message, None, False, False)
            continue
        base, rcode, text, so, la, sa = _taxability(rule, p.tax_code, unit, ln.qty, p.holiday, on)
        if base:
            item_taxable += amount
        add_line(i, lid, p.sku, p.name, p.tax_code, ln.qty, unit, amount, base, rcode, text, so, la, sa)

    # 5. shipping and handling
    handling_mode = rule.shipping if rule.handling == "same_as_shipping" else rule.handling
    for label, charge, mode in (("shipping", cart.shipping, rule.shipping), ("handling", cart.handling, handling_mode)):
        if charge <= 0:
            continue
        idx = len(lines_out)
        code = label.upper()
        if collector != "seller" or exempt_all:
            add_line(idx, code, code, label, code, 1, charge, charge, 0, collector_code if collector != "seller" else cert_result.code,
                     "not taxed: see order notes", None, False, False)
            continue
        if mode == "taxable":
            add_line(idx, code, code, label, code, 1, charge, charge, charge * 100, f"{code}.TAXABLE", f"{label} is taxable in {st}", None, True, True)
        elif mode == "proportional" and item_total > 0:
            base = half_up(charge * item_taxable, item_total)
            add_line(idx, code, code, label, code, 1, charge, charge, base * 100, f"{code}.PROPORTIONAL",
                     f"{label} is taxable in the share of taxable items: ${charge / 100:,.2f} x {item_taxable / 100:,.2f}/{item_total / 100:,.2f} "
                     f"= ${base / 100:,.2f}", None, True, True)
        else:
            add_line(idx, code, code, label, code, 1, charge, charge, 0, f"{code}.EXEMPT", f"separately stated {label} is exempt in {st}", None, False, False)

    # 6. rounding
    nj = len(rates)
    if rule.rounding == "invoice":
        total = half_up(sum(c[2] for c in cells))
        parts = allocate([c[2] for c in cells], total)
    else:
        parts = []
        for li in range(len(lines_out)):
            seg = [c[2] for c in cells[li * nj:(li + 1) * nj]]
            parts += allocate(seg, half_up(sum(seg)))
        total = sum(parts)
    by_jur = [0] * nj
    base_by_jur = [0] * nj
    exact_by_jur = [0] * nj
    due_by_jur = [0] * nj
    for (li, ji, units, b, due), cents in zip(cells, parts, strict=True):
        by_jur[ji] += cents
        base_by_jur[ji] += b
        exact_by_jur[ji] += units
        due_by_jur[ji] += due
        lo = lines_out[li]
        lo["tax"] += cents
        if explain:
            lo["jurisdictions"][ji]["tax"] = cents
    subtotal = item_total + max(cart.shipping, 0) + max(cart.handling, 0)
    out = {
        "cart_id": cart.id, "date": on.isoformat(), "state": st, "channel": cart.channel, "customer_id": cart.customer_id,
        "collector": collector, "collector_code": collector_code, "subtotal": subtotal, "tax": total, "total": subtotal + total,
        "rounding": rule.rounding, "lines": lines_out,
        "jurisdictions": [{"id": j.id, "name": j.name, "kind": j.kind, "rate_ppm": e.rate_ppm if e else 0, "tax": by_jur[ji],
                           "base_x100": base_by_jur[ji], "exact_units": exact_by_jur[ji], "due_units": due_by_jur[ji]}
                          for ji, (j, e) in enumerate(rates)],
    }
    if explain:
        for ji, row in enumerate(out["jurisdictions"]):
            row["rate"] = fmt_rate(row["rate_ppm"])
            row["taxable_amount"] = half_up(base_by_jur[ji], 100)
        out["sourcing"] = sourcing
        out["certificate"] = None if cert_result is None else {"applies": cert_result.applies, "code": cert_result.code, "message": cert_result.message}
        out["notes"] = notes
        out["rounding_note"] = ("each line is rounded half up to the cent, then split across jurisdictions" if rule.rounding == "line" else
                                "exact tax is summed over the invoice and rounded half up once, then split across lines and jurisdictions")
        out["rule_versions"] = {"ruleset": RULESET_VERSION, "fingerprint": fingerprint(), "state_rule": rule.version,
                                "rates": {j.id: e.effective_from.isoformat() if e else None for j, e in rates}, "as_loaded": ctx.as_loaded}
    return out


def explain_text(r: dict) -> str:
    """Plain text rendering of a calculation, used by the CLI and the README."""
    out = [f"cart {r['cart_id']} on {r['date']} to {r['state']}, channel {r['channel']}, collector: {r['collector']}"]
    s = r["sourcing"]
    out.append(f"  sourcing: {s['rule']} ({s['why']}); lookup by {s['lookup']}")
    if s.get("override"):
        out.append(f"    street override: {s['override']}; ZIP-only would have used {', '.join(s['zip_only_jurisdictions'])}")
    out.append("  jurisdictions: " + ", ".join(f"{j['name']} {j['rate']}" for j in r["jurisdictions"]))
    for n in r["notes"]:
        out.append(f"  note: {n}")
    for ln in r["lines"]:
        out.append(f"  {ln['id']:<8} {ln['name'][:30]:<30} {ln['qty']} x ${ln['unit_price'] / 100:>8,.2f}  {ln['rule_code']:<22} "
                   f"taxable ${ln['taxable_amount'] / 100:>8,.2f}  tax ${ln['tax'] / 100:>6,.2f}")
        out.append(f"           {ln['rule']}")
    out.append(f"  rounding: {r['rounding']} ({r['rounding_note']})")
    out.append("  by jurisdiction: " + ", ".join(f"{j['id']} ${j['tax'] / 100:,.2f}" for j in r["jurisdictions"]))
    out.append(f"  subtotal ${r['subtotal'] / 100:,.2f}  tax ${r['tax'] / 100:,.2f}  total ${r['total'] / 100:,.2f}")
    v = r["rule_versions"]
    out.append(f"  rules: {v['ruleset']} ({v['fingerprint']}), {v['state_rule']}")
    return "\n".join(out)
