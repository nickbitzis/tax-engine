"""Address to jurisdiction lookup: a ZIP table plus a street-level override table for split ZIPs.

A ZIP code is a postal route, not a tax boundary. ZIPs ending in 02 and 05 in
every state with local tax cross a city line: the ZIP table holds the
jurisdictions for most of the ZIP, and the override table holds street ranges
that sit inside the other city (and its district). A ZIP-only lookup charges
those addresses the wrong local rate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache

from taxengine.rules import NO_LOCAL_TAX, STATES, jurisdictions

_STREET = re.compile(r"^\s*(\d+)\s+(.+?)\s*$")
_SUFFIX = {"street": "st", "avenue": "ave", "road": "rd", "drive": "dr", "boulevard": "blvd", "lane": "ln", "way": "way"}


@dataclass(frozen=True, slots=True)
class Override:
    zip: str
    street: str  # normalized street name
    low: int
    high: int
    jurisdictions: tuple[str, ...]
    note: str


@dataclass(frozen=True, slots=True)
class Resolution:
    state: str
    zip: str
    jurisdictions: tuple[str, ...]  # state first, then county, city, districts
    method: str  # zip | street_override | state_only
    zip_only: tuple[str, ...]
    override: Override | None = None

    @property
    def differs_from_zip_only(self) -> bool:
        return self.jurisdictions != self.zip_only


class AddressError(ValueError):
    pass


def normalize_street(name: str) -> str:
    words = re.sub(r"[.,]", "", name.lower()).split()
    return " ".join(_SUFFIX.get(w, w) for w in words)


@cache
def zip_table() -> dict[str, tuple[str, ...]]:
    js = jurisdictions()
    out: dict[str, tuple[str, ...]] = {}
    for st, (_, prefix, _) in STATES.items():
        for k in range(1, 7):
            z = f"{prefix}{k:02d}"
            if st in NO_LOCAL_TAX or f"{st}-C1" not in js:
                out[z] = (st,)
                continue
            out[z] = {
                1: (st, f"{st}-C1", f"{st}-CITY1", f"{st}-TD"),
                2: (st, f"{st}-C1", f"{st}-CITY2"),
                3: (st, f"{st}-C1"),
                4: (st, f"{st}-C2", f"{st}-CITY3", f"{st}-SD"),
                5: (st, f"{st}-C2"),
                6: (st, f"{st}-C2", f"{st}-CITY3"),
            }[k]
    return out


@cache
def overrides() -> tuple[Override, ...]:
    js = jurisdictions()
    out = []
    for st, (_, prefix, _) in STATES.items():
        if st in NO_LOCAL_TAX or f"{st}-C1" not in js:
            continue
        city1, city2, city3 = js[f"{st}-CITY1"].name, js[f"{st}-CITY2"].name, js[f"{st}-CITY3"].name
        out.append(Override(f"{prefix}02", "main st", 1, 999, (st, f"{st}-C1", f"{st}-CITY1", f"{st}-TD"),
                            f"Main St 1 to 999 is inside {city1} and its transit district, not {city2}"))
        out.append(Override(f"{prefix}05", "harbor rd", 500, 1999, (st, f"{st}-C2", f"{st}-CITY3", f"{st}-SD"),
                            f"Harbor Rd 500 to 1999 was annexed by {city3} and is in its stadium district"))
    return tuple(out)


@cache
def _override_index() -> dict[tuple[str, str], list[Override]]:
    idx: dict[tuple[str, str], list[Override]] = {}
    for o in overrides():
        idx.setdefault((o.zip, o.street), []).append(o)
    return idx


@cache
def zip_state() -> dict[str, str]:
    return {f"{p}{k:02d}": st for st, (_, p, _) in STATES.items() for k in range(1, 7)}


def resolve(state: str, zip_code: str, street: str = "") -> Resolution:
    zip5 = (zip_code or "").strip()[:5]
    table = zip_table()
    if zip5 not in table:
        raise AddressError(f"unknown ZIP {zip5!r} (synthetic ZIPs are the state prefix plus 01 to 06)")
    if zip_state()[zip5] != state:
        raise AddressError(f"ZIP {zip5} is in {zip_state()[zip5]}, not {state}")
    base = table[zip5]
    m = _STREET.match(street or "")
    if m:
        num, name = int(m.group(1)), normalize_street(m.group(2))
        for o in _override_index().get((zip5, name), ()):
            if o.low <= num <= o.high:
                return Resolution(state, zip5, o.jurisdictions, "street_override", base, o)
    return Resolution(state, zip5, base, "zip" if len(base) > 1 else "state_only", base)


@cache
def resolve_cached(state: str, zip_code: str, street: str) -> Resolution:
    return resolve(state, zip_code, street)
