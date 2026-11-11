from __future__ import annotations

from datetime import date
from fractions import Fraction

from taxengine.calc import Address, Cart, Context, LineIn, calculate
from taxengine.rules import STATES, jurisdictions

MARCH = date(2026, 3, 10)


def z(state: str, k: int = 1) -> str:
    return f"{STATES[state][1]}{k:02d}"


def mk(state: str, lines, on: date = MARCH, zk: int = 1, street: str = "250 Oak Ave", shipping: int = 0, handling: int = 0,
       customer: str | None = None, channel: str = "direct", ship_from: str | None = None) -> Cart:
    ls = [LineIn(s, q, p) for s, q, p in ((x if isinstance(x, tuple) else (x, 1, None)) for x in lines)]
    return Cart("t", on, Address(street, state, z(state, zk)), ls, shipping, handling, customer, channel, ship_from)


def calc(cart: Cart, ctx: Context | None = None) -> dict:
    return calculate(cart, ctx or Context())


def rate(jid: str, on: date = MARCH) -> Fraction:
    return Fraction(jurisdictions()[jid].rate_at(on), 1_000_000)


def combined(ids, on: date = MARCH) -> Fraction:
    return sum((rate(j, on) for j in ids), Fraction(0))


def cents(x: Fraction) -> int:
    """Half up to the cent, independent of the engine's integer code."""
    return int(x + Fraction(1, 2)) if x >= 0 else -int(-x + Fraction(1, 2))
