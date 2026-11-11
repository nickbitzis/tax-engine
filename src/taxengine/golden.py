"""Golden carts: about 50 carts that exercise every rule, with the exact expected tax per line.

The CI gate recomputes every cart and fails on any difference of one cent. When a
rule change is intended, regenerate the file (`taxengine golden`) and review the
diff in the pull request: every changed amount is a changed invoice.
"""

from __future__ import annotations

import json
import random
from datetime import date
from pathlib import Path

from taxengine.calc import Address, Cart, Context, LineIn, calculate
from taxengine.catalog import PRODUCTS
from taxengine.certs import Certificate
from taxengine.rules import RULESET_VERSION, STATES, fingerprint

GOLDEN_CERTS = [
    Certificate("G-RESALE", "G1", "Harbor Outfitters", "resale", ("NY", "TX", "WA"), date(2024, 1, 1), date(2028, 12, 31), "valid", "NY-1"),
    Certificate("G-EXPIRED", "G2", "Summit Home Goods", "resale", ("OH",), date(2022, 4, 1), date(2026, 3, 31), "valid", "OH-2"),
    Certificate("G-NONPROFIT", "G3", "Fernwood Food Bank", "nonprofit", ("CA", "IL", "OH"), date(2024, 6, 1), date(2029, 5, 31), "valid", "CA-3"),
    Certificate("G-PENDING", "G4", "Granite School District", "government", ("GA",), date(2026, 1, 10), date(2036, 1, 9), "pending", "GA-4"),
    Certificate("G-GOV", "G5", "Hollis Parks Department", "government", ("FL",), date(2025, 1, 1), date(2035, 1, 1), "valid", "FL-5"),
]


def _z(st: str, k: int) -> str:
    return f"{STATES[st][1]}{k:02d}"


def _c(cid: str, why: str, d: str, st: str, lines: list, zk: int = 1, street: str = "250 Oak Ave", ship: int = 0, hand: int = 0,
       customer: str | None = None, channel: str = "direct", ship_from: str | None = None) -> dict:
    return {"id": cid, "why": why, "date": d, "ship_to": {"street": street, "state": st, "zip": _z(st, zk)},
            "lines": [{"sku": s, "qty": q, **({"price": p} if p is not None else {})} for s, q, p in lines],
            "shipping": ship, "handling": hand, "customer_id": customer, "channel": channel, "ship_from": ship_from}


def cases() -> list[dict]:
    m = "2026-03-10"
    out = [
        _c("G01", "NY clothing under the per item threshold is exempt", m, "NY", [("TEE-01", 3, None)]),
        _c("G02", "NY clothing at or over the threshold is fully taxable", m, "NY", [("COAT-01", 1, None)]),
        _c("G03", "NY threshold edge: exactly $110.00 is taxable, $109.99 is not", m, "NY", [("SHOE-01", 1, 11_000), ("SHOE-01", 1, 10_999)]),
        _c("G04", "RI clothing threshold is higher", m, "RI", [("JKT-01", 1, None), ("COAT-01", 1, 26_000)]),
        _c("G05", "MA taxes only the part of an item over the threshold", m, "MA", [("COAT-01", 2, None)]),
        _c("G06", "MA item under the threshold is exempt", m, "MA", [("JEAN-01", 1, None)]),
        _c("G07", "PA clothing is exempt", m, "PA", [("COAT-01", 1, None), ("LAMP-01", 1, None)]),
        _c("G08", "NY split ZIP: Main St 120 is inside the other city and the transit district", m, "NY", [("SPKR-01", 1, None)], 2, "120 Main St"),
        _c("G09", "NY split ZIP: Main St 1500 is outside the override range", m, "NY", [("SPKR-01", 1, None)], 2, "1500 Main St"),
        _c("G10", "CO split ZIP: Harbor Rd 700 was annexed", m, "CO", [("DESK-01", 1, None)], 5, "700 Harbor Road"),
        _c("G11", "TX origin sourcing: in-state sale uses the Dallas warehouse rates", m, "TX", [("LAMP-01", 1, None)], 4, "9 Mill St"),
        _c("G12", "PA before the PA warehouse opens: ships from TX, destination rates", "2026-04-15", "PA", [("SPKR-01", 1, None)], 1),
        _c("G13", "PA after the PA warehouse opens: origin rates", "2026-06-15", "PA", [("SPKR-01", 1, None)], 1),
        _c("G14", "CA invoice rounding over many small lines", m, "CA", [("PENS-12", 1, None), ("NOTE-03", 1, None), ("FLSH-01", 1, None),
                                                                         ("SKIL-01", 1, None), ("LAMP-01", 1, 3_333)]),
        _c("G15", "TX line rounding, same lines as G14", m, "TX", [("PENS-12", 1, None), ("NOTE-03", 1, None), ("FLSH-01", 1, None),
                                                                   ("SKIL-01", 1, None), ("LAMP-01", 1, 3_333)], 1, ship_from="TX-DAL"),
        _c("G16", "IL groceries at the reduced state rate, locals in full", m, "IL", [("COFF-2", 2, None), ("OIL-01", 1, None)]),
        _c("G17", "MS groceries fully taxable", m, "MS", [("GRAN-04", 3, None)], 4),
        _c("G18", "UT groceries reduced (destination: ships from TX)", m, "UT", [("COFF-2", 1, None)], 4),
        _c("G19", "Prescriptions exempt everywhere", m, "CA", [("RX-REFILL", 2, None), ("RX-PEN", 1, None)]),
        _c("G20", "CA digital downloads exempt", m, "CA", [("EBOOK-01", 1, None), ("APP-PHOTO", 1, None)]),
        _c("G21", "WI digital downloads taxable", m, "WI", [("EBOOK-01", 1, None), ("ALBUM-01", 2, None)], 4),
        _c("G22", "TX SaaS taxed on 80% of the price", m, "TX", [("SAAS-STOR", 1, None)], 1),
        _c("G23", "CT SaaS at a reduced state rate", m, "CT", [("SAAS-STOR", 1, None), ("SAAS-EDIT", 1, None)]),
        _c("G24", "NY SaaS fully taxable", m, "NY", [("SAAS-STOR", 1, None)], 3),
        _c("G25", "MN SaaS not taxable", m, "MN", [("SAAS-STOR", 1, None)]),
        _c("G26", "WA installation taxable", m, "WA", [("TV-55", 1, None), ("SVC-MOUNT", 1, None)], 6),
        _c("G27", "GA separately stated installation exempt", m, "GA", [("TV-55", 1, None), ("SVC-MOUNT", 1, None)]),
        _c("G28", "NJ shipping taxable", m, "NJ", [("SKIL-01", 1, None)], ship=699),
        _c("G29", "ME shipping exempt, handling taxable", m, "ME", [("SKIL-01", 1, None)], ship=699, hand=199),
        _c("G30", "MI shipping taxable in the share of taxable items", m, "MI", [("SKIL-01", 1, None), ("RX-REFILL", 1, None)], ship=699),
        _c("G31", "RI proportional shipping with an exempt clothing item", m, "RI", [("TEE-01", 1, None), ("LAMP-01", 1, None)], ship=699),
        _c("G32", "CA shipping exempt, handling taxable", m, "CA", [("SKIL-01", 1, None)], 2, ship=699, hand=199),
        _c("G33", "FL marketplace sale: the marketplace collects", m, "FL", [("TV-55", 1, None)], channel="marketplace"),
        _c("G34", "MO marketplace sale before the facilitator rule: seller collects", "2025-12-15", "MO", [("LAMP-01", 1, None)],
           channel="marketplace"),
        _c("G35", "MO marketplace sale after the facilitator rule", "2026-01-15", "MO", [("LAMP-01", 1, None)], channel="marketplace"),
        _c("G36", "OR has no sales tax", m, "OR", [("TV-55", 1, None)]),
        _c("G37", "FL back to school holiday: clothing under the cap", "2026-08-10", "FL", [("JEAN-01", 2, None)]),
        _c("G38", "FL holiday: clothing over the cap is taxable", "2026-08-10", "FL", [("COAT-01", 1, None)]),
        _c("G39", "FL holiday: laptop under the computer cap", "2026-08-10", "FL", [("LAPT-14", 1, None)]),
        _c("G40", "AL holiday: state exempt, local taxes still apply", "2026-07-18", "AL", [("JEAN-01", 1, None)]),
        _c("G41", "SC holiday with no item cap", "2026-08-08", "SC", [("LAPT-14", 1, None)], 4),
        _c("G42", "FL disaster preparedness holiday: generator", "2026-06-05", "FL", [("GENR-01", 1, None), ("FLSH-01", 2, None)]),
        _c("G43", "Resale certificate covers NY", m, "NY", [("TV-55", 2, None)], ship=0, customer="G1"),
        _c("G44", "Resale certificate does not cover NJ", m, "NJ", [("TV-55", 1, None)], customer="G1"),
        _c("G45", "Expired certificate: OH sale after expiry is taxed", "2026-04-15", "OH", [("CHAIR-01", 1, None)], customer="G2"),
        _c("G46", "Same OH certificate before expiry", "2026-03-15", "OH", [("CHAIR-01", 1, None)], customer="G2"),
        _c("G47", "CA does not accept nonprofit certificates (illustrative)", m, "CA", [("CHAIR-01", 1, None)], customer="G3"),
        _c("G48", "OH accepts the same nonprofit certificate", m, "OH", [("CHAIR-01", 1, None)], customer="G3"),
        _c("G49", "Pending certificate: taxed until validated", m, "GA", [("CHAIR-01", 1, None)], customer="G4"),
        _c("G50", "NC state rate change on Jan 1: before", "2025-12-31", "NC", [("DESK-01", 1, None)]),
        _c("G51", "NC state rate change on Jan 1: after", "2026-01-01", "NC", [("DESK-01", 1, None)]),
        _c("G52", "KS city rate change on Jul 1 (correct rate, even though production loaded it Jul 8)", "2026-07-03", "KS",
           [("DESK-01", 1, None)], 1),
        _c("G53", "Government certificate in FL", m, "FL", [("GENR-01", 1, None)], customer="G5"),
    ]
    rng = random.Random(53)
    skus = sorted(PRODUCTS)
    states = sorted(STATES)
    for i in range(54, 61):
        st = rng.choice(states)
        lines = [(rng.choice(skus), rng.randint(1, 3), None) for _ in range(rng.randint(2, 4))]
        out.append(_c(f"G{i}", "seeded mixed cart", f"2026-{rng.randint(1, 10):02d}-{rng.randint(1, 28):02d}", st, lines, rng.randint(1, 6),
                      f"{rng.randint(1, 1999)} {rng.choice(['Main St', 'Harbor Rd', 'Oak Ave'])}", rng.choice([0, 699]), rng.choice([0, 0, 199])))
    return out


def cart_from_dict(d: dict) -> Cart:
    s = d["ship_to"]
    return Cart(d.get("id") or "cart", date.fromisoformat(str(d["date"])), Address(s.get("street", ""), s["state"].upper(), str(s["zip"])),
                [LineIn(ln["sku"], int(ln.get("qty", 1)), ln.get("price"), ln.get("id")) for ln in d["lines"]],
                int(d.get("shipping") or 0), int(d.get("handling") or 0), d.get("customer_id"), d.get("channel") or "direct", d.get("ship_from"))


def golden_context() -> Context:
    ctx = Context(registrations=None)
    for c in GOLDEN_CERTS:
        ctx.certificates.setdefault(c.customer_id, []).append(c)
    return ctx


def compute(case: dict) -> dict:
    r = calculate(cart_from_dict(case), golden_context())
    return {"tax": r["tax"], "collector": r["collector"], "lines": {ln["id"]: ln["tax"] for ln in r["lines"]},
            "rules": [ln["rule_code"] for ln in r["lines"]]}


def build() -> dict:
    return {"ruleset": RULESET_VERSION, "fingerprint": fingerprint(), "note": "Illustrative rules, not current law.",
            "carts": [{**c, "expected": compute(c)} for c in cases()]}


def write(path: str | Path) -> int:
    data = build()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=1) + "\n")
    return len(data["carts"])


def gate(path: str | Path) -> tuple[bool, list[str], int]:
    data = json.loads(Path(path).read_text())
    problems = []
    for c in data["carts"]:
        got = compute(c)
        exp = c["expected"]
        if got != exp:
            diffs = [f"{k}: expected {exp['lines'].get(k)} got {v}" for k, v in got["lines"].items() if exp["lines"].get(k) != v]
            problems.append(f"{c['id']} ({c['why']}): tax expected {exp['tax']} got {got['tax']}; " + "; ".join(diffs or [f"rules {got['rules']}"]))
    if data.get("fingerprint") != fingerprint() and not problems:
        problems_note = f"rule fingerprint changed ({data.get('fingerprint')} -> {fingerprint()}) but every golden amount still matches"
        return True, [problems_note], len(data["carts"])
    return not problems, problems, len(data["carts"])
