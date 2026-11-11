"""Illustrative tax rules as data. NOT CURRENT LAW.

Every rate, threshold, holiday and taxability rule here is made up for a demo.
State rates are generated from the state code, local jurisdictions and their
names are synthetic, and the rule assignments were chosen to give variety, not
to match any state. Do not use any of this to file a return.

Rates are integers in parts per million of the price (6.25% = 62500), so tax
math is exact integer arithmetic until the final rounding to cents.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from functools import cache

RULESET_VERSION = "illustrative-2026.11-r4"
PPM = 1_000_000

# code: (name, 3 digit ZIP prefix, population weight for the simulation)
STATES: dict[str, tuple[str, str, int]] = {
    "AL": ("Alabama", "350", 50), "AK": ("Alaska", "995", 7), "AZ": ("Arizona", "850", 73), "AR": ("Arkansas", "716", 30),
    "CA": ("California", "900", 390), "CO": ("Colorado", "800", 58), "CT": ("Connecticut", "060", 36), "DE": ("Delaware", "197", 10),
    "DC": ("District of Columbia", "200", 7), "FL": ("Florida", "330", 220), "GA": ("Georgia", "300", 109), "HI": ("Hawaii", "967", 14),
    "ID": ("Idaho", "832", 19), "IL": ("Illinois", "606", 126), "IN": ("Indiana", "460", 68), "IA": ("Iowa", "500", 32),
    "KS": ("Kansas", "660", 29), "KY": ("Kentucky", "400", 45), "LA": ("Louisiana", "700", 46), "ME": ("Maine", "040", 14),
    "MD": ("Maryland", "210", 62), "MA": ("Massachusetts", "021", 70), "MI": ("Michigan", "480", 100), "MN": ("Minnesota", "550", 57),
    "MS": ("Mississippi", "386", 29), "MO": ("Missouri", "630", 61), "MT": ("Montana", "590", 11), "NE": ("Nebraska", "680", 20),
    "NV": ("Nevada", "889", 31), "NH": ("New Hampshire", "030", 14), "NJ": ("New Jersey", "070", 93), "NM": ("New Mexico", "870", 21),
    "NY": ("New York", "100", 196), "NC": ("North Carolina", "270", 106), "ND": ("North Dakota", "580", 8), "OH": ("Ohio", "430", 118),
    "OK": ("Oklahoma", "730", 40), "OR": ("Oregon", "970", 42), "PA": ("Pennsylvania", "150", 130), "RI": ("Rhode Island", "028", 11),
    "SC": ("South Carolina", "290", 52), "SD": ("South Dakota", "570", 9), "TN": ("Tennessee", "370", 70), "TX": ("Texas", "750", 300),
    "UT": ("Utah", "840", 33), "VT": ("Vermont", "050", 6), "VA": ("Virginia", "220", 86), "WA": ("Washington", "980", 77),
    "WV": ("West Virginia", "247", 18), "WI": ("Wisconsin", "530", 59), "WY": ("Wyoming", "820", 6),
}

NO_SALES_TAX = {"AK", "DE", "MT", "NH", "OR"}
NO_LOCAL_TAX = {"CT", "DC", "IN", "KY", "ME", "MD", "MA", "MI", "RI", "VT", "WV", "NJ"}

TAX_CODES = {
    "GEN": "General merchandise",
    "CLOTH": "Clothing and footwear",
    "GROC": "Groceries (food for home consumption)",
    "RX": "Prescription drugs",
    "DIGITAL": "Digital downloads (books, music, software)",
    "SAAS": "Software as a service subscriptions",
    "INSTALL": "Installation services, separately stated",
}


@dataclass(frozen=True, slots=True)
class RateEntry:
    effective_from: date
    rate_ppm: int
    loaded_on: date  # the day the change reached the production rate table


@dataclass(frozen=True, slots=True)
class Jurisdiction:
    id: str
    state: str
    kind: str  # state, county, city, district
    name: str
    rates: tuple[RateEntry, ...]

    def entry_at(self, d: date, as_loaded: bool = False) -> RateEntry | None:
        hit = None
        for e in self.rates:
            if e.effective_from <= d and (not as_loaded or e.loaded_on <= d):
                hit = e
        return hit

    def rate_at(self, d: date, as_loaded: bool = False) -> int:
        e = self.entry_at(d, as_loaded)
        return e.rate_ppm if e else 0


@dataclass(frozen=True, slots=True)
class Holiday:
    name: str
    start: date
    end: date
    caps: dict[str, int | None]  # holiday category -> per item price cap in cents (None = no cap)
    local_too: bool = True


@dataclass(frozen=True, slots=True)
class StateRule:
    state: str
    version: str
    has_tax: bool
    sourcing: str = "destination"  # destination | origin (origin applies only to sales shipped from inside the state)
    rounding: str = "line"  # line | invoice
    clothing: str = "taxable"  # taxable | exempt | below_threshold_exempt | excess_over_threshold
    clothing_threshold: int = 0  # cents per item
    groceries: str = "exempt"  # exempt | taxable | reduced
    grocery_state_ppm: int = 0  # reduced state rate (locals still apply)
    digital: str = "taxable"  # taxable | exempt
    saas: str = "exempt"  # exempt | taxable | partial | reduced
    saas_base_pct: int = 100  # partial: share of the price that is taxable
    saas_state_ppm: int = 0  # reduced state rate
    install: str = "exempt"  # exempt | taxable
    shipping: str = "exempt"  # exempt | taxable | proportional
    handling: str = "same_as_shipping"  # exempt | taxable | same_as_shipping
    threshold_usd: int = 100_000
    threshold_txns: int | None = 200
    threshold_mode: str = "or"  # or | and
    count_marketplace: bool = True
    registration: str = "next_month"  # next_month | 30_days | next_quarter
    filing: str = "quarterly"  # monthly | quarterly
    mpf_effective: date = date(2019, 1, 1)
    exempt_types: tuple[str, ...] = ("resale", "nonprofit", "government")
    holidays: tuple[Holiday, ...] = field(default_factory=tuple)


def _h(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest()[:8], 16)


def _pct(p: str) -> int:
    whole, _, frac = p.partition(".")
    frac = (frac + "0000")[:4]
    return int(whole) * 10_000 + int(frac)


# Rule overrides by state. Everything not listed uses the StateRule default.
OVERRIDES: dict[str, dict] = {
    "TX": dict(sourcing="origin", saas="partial", saas_base_pct=80, shipping="taxable", install="taxable", threshold_usd=500_000,
               threshold_txns=None, count_marketplace=False, filing="monthly"),
    "PA": dict(sourcing="origin", clothing="exempt", saas="taxable", shipping="taxable", filing="monthly"),
    "AZ": dict(sourcing="origin", threshold_txns=None, digital="exempt"),
    "UT": dict(sourcing="origin", groceries="reduced", grocery_state_ppm=17_500, saas="taxable"),
    "CA": dict(rounding="invoice", digital="exempt", threshold_usd=500_000, threshold_txns=None, count_marketplace=False,
               handling="taxable", filing="monthly", exempt_types=("resale", "government")),
    "NY": dict(rounding="invoice", clothing="below_threshold_exempt", clothing_threshold=11_000, saas="taxable", shipping="taxable",
               threshold_usd=500_000, threshold_txns=100, threshold_mode="and", filing="monthly"),
    "RI": dict(clothing="below_threshold_exempt", clothing_threshold=25_000, saas="taxable", shipping="proportional"),
    "MA": dict(clothing="excess_over_threshold", clothing_threshold=17_500, digital="exempt", rounding="invoice", threshold_txns=None),
    "MN": dict(clothing="exempt", shipping="proportional", saas="exempt"),
    "NJ": dict(clothing="exempt", shipping="taxable", install="taxable", filing="monthly"),
    "VT": dict(clothing="exempt", saas="taxable", shipping="proportional", threshold_txns=None),
    "IL": dict(groceries="reduced", grocery_state_ppm=10_000, digital="exempt", rounding="invoice", filing="monthly",
               exempt_types=("resale", "government")),
    "TN": dict(groceries="reduced", grocery_state_ppm=40_000, saas="taxable", shipping="proportional", registration="30_days",
               holidays=(Holiday("Back to school", date(2026, 7, 24), date(2026, 7, 26), {"clothing": 10_000, "school_supplies": 10_000,
                                                                                         "computers": 150_000}),)),
    "VA": dict(groceries="reduced", grocery_state_ppm=10_000, digital="exempt",
               holidays=(Holiday("Sales tax holiday", date(2026, 8, 7), date(2026, 8, 9), {"clothing": 10_000, "school_supplies": 2_000,
                                                                                            "emergency": 6_000}),)),
    "AR": dict(groceries="reduced", grocery_state_ppm=1_250, shipping="taxable"),
    "MS": dict(groceries="taxable", threshold_usd=250_000, threshold_txns=None, shipping="taxable", saas="taxable"),
    "AL": dict(groceries="taxable", threshold_usd=250_000, threshold_txns=None, registration="next_quarter",
               holidays=(Holiday("Back to school", date(2026, 7, 17), date(2026, 7, 19), {"clothing": 10_000, "computers": 75_000,
                                                                                         "school_supplies": 5_000}, local_too=False),)),
    "SD": dict(groceries="taxable", saas="taxable", install="taxable", shipping="taxable"),
    "ID": dict(groceries="taxable", threshold_txns=None),
    "KS": dict(groceries="reduced", grocery_state_ppm=20_000, shipping="taxable", threshold_txns=None),
    "WA": dict(rounding="invoice", saas="taxable", install="taxable", shipping="taxable", filing="monthly"),
    "HI": dict(groceries="taxable", saas="taxable", install="taxable", shipping="taxable", digital="taxable"),
    "NM": dict(saas="taxable", install="taxable", shipping="taxable", digital="taxable"),
    "WV": dict(saas="taxable", install="taxable", shipping="taxable"),
    "CT": dict(saas="reduced", saas_state_ppm=10_000, install="taxable", shipping="taxable"),
    "OH": dict(saas="taxable", install="taxable", shipping="taxable", filing="monthly",
               holidays=(Holiday("Expanded holiday", date(2026, 8, 1), date(2026, 8, 3), {"clothing": 7_500, "school_supplies": 2_000}),)),
    "DC": dict(saas="taxable", shipping="taxable", threshold_txns=None),
    "IA": dict(saas="taxable", shipping="exempt", handling="taxable", threshold_txns=None),
    "FL": dict(digital="exempt", threshold_txns=None, handling="taxable", rounding="invoice", filing="monthly",
               holidays=(Holiday("Back to school", date(2026, 8, 1), date(2026, 8, 31), {"clothing": 10_000, "school_supplies": 5_000,
                                                                                        "computers": 150_000}),
                         Holiday("Disaster preparedness", date(2026, 6, 1), date(2026, 6, 14), {"emergency": 75_000}))),
    "GA": dict(digital="exempt", shipping="taxable", filing="monthly"),
    "MI": dict(digital="exempt", shipping="proportional"),
    "NV": dict(digital="exempt", registration="30_days"),
    "MO": dict(digital="exempt", mpf_effective=date(2026, 1, 1), threshold_txns=None),
    "OK": dict(digital="exempt", registration="30_days"),
    "SC": dict(digital="exempt", saas="taxable",
               holidays=(Holiday("Back to school", date(2026, 8, 7), date(2026, 8, 9), {"clothing": None, "school_supplies": None,
                                                                                       "computers": None}),)),
    "MD": dict(shipping="proportional", rounding="invoice", exempt_types=("resale", "government"),
               holidays=(Holiday("Shop Maryland", date(2026, 8, 9), date(2026, 8, 15), {"clothing": 10_000}),)),
    "CO": dict(rounding="invoice", threshold_txns=None, registration="next_quarter"),
    "NC": dict(shipping="taxable", install="taxable", filing="monthly"),
    "KY": dict(shipping="taxable", saas="exempt", exempt_types=("resale", "government")),
    "NE": dict(shipping="taxable", install="taxable"),
    "ND": dict(shipping="taxable"),
    "WI": dict(shipping="taxable", digital="taxable"),
    "IN": dict(shipping="proportional", exempt_types=("resale", "government")),
    "LA": dict(registration="next_quarter", threshold_txns=None),
    "ME": dict(shipping="exempt", handling="taxable"),
    "WY": dict(shipping="proportional", threshold_txns=None),
}

# One state rate change during the simulated year (illustrative).
STATE_RATE_CHANGES = {"NC": (date(2026, 1, 1), "+0.25")}
# Local rate changes during the year. The KS city change reached the production table a week late.
LOCAL_RATE_CHANGES = {
    "WA-CITY1": (date(2026, 4, 1), "+0.10", 0),
    "CA-TD": (date(2026, 4, 1), "+0.25", 0),
    "GA-C1": (date(2026, 7, 1), "+0.50", 0),
    "KS-CITY1": (date(2026, 7, 1), "+1.00", 7),
    "NY-CITY1": (date(2026, 1, 1), "+0.125", 0),
}

COUNTY_NAMES = ["Alder", "Birch", "Cedar", "Dogwood", "Elmwood", "Fir", "Hawthorn", "Juniper", "Larch", "Maplewood", "Oakmont",
                "Pinecrest", "Redbud", "Sumac", "Tamarack", "Willowbend", "Hazelton", "Aspenridge", "Buckeye", "Cottonwood"]
CITY_NAMES = ["Ashby", "Brookvale", "Carrow", "Dunmere", "Easton Falls", "Fallbrook Crossing", "Glenhollow", "Harlow", "Ivydale",
              "Kestrel", "Lanford", "Millbrook", "Northgate", "Orchard Hill", "Prestwick", "Quarry", "Riverton Bend", "Stonebridge",
              "Thornbury", "Upton Mills", "Vale", "Westmere", "Yarrow", "Zephyr Point"]

LAUNCH_EPOCH = date(2018, 1, 1)


@cache
def state_rules() -> dict[str, StateRule]:
    out = {}
    for st in STATES:
        kw = dict(OVERRIDES.get(st, {}))
        n = len(kw)
        out[st] = StateRule(state=st, version=f"{st}-2026.{1 + n % 4}", has_tax=st not in NO_SALES_TAX, **kw)
    return out


def _rate_entries(jid: str, base_pct: str, change: tuple | None) -> tuple[RateEntry, ...]:
    base = _pct(base_pct)
    entries = [RateEntry(LAUNCH_EPOCH, base, LAUNCH_EPOCH)]
    if change:
        eff, delta, lag = change
        sign = -1 if delta.startswith("-") else 1
        new = base + sign * _pct(delta.lstrip("+-"))
        entries.append(RateEntry(eff, new, eff + timedelta(days=lag)))
    return tuple(entries)


def _local_pct(jid: str, lo: int, hi: int, step: int = 25) -> str:
    """Synthetic local rate between lo and hi hundredths of a percent."""
    n = (hi - lo) // step + 1
    v = lo + (_h(jid) % n) * step
    return f"{v // 100}.{v % 100:02d}"


@cache
def jurisdictions() -> dict[str, Jurisdiction]:
    out: dict[str, Jurisdiction] = {}
    for st, (name, _, _) in STATES.items():
        if st in NO_SALES_TAX:
            out[st] = Jurisdiction(st, st, "state", name, (RateEntry(LAUNCH_EPOCH, 0, LAUNCH_EPOCH),))
            continue
        v = 400 + (_h(st) % 13) * 25  # 4.00% to 7.00%
        chg = STATE_RATE_CHANGES.get(st)
        out[st] = Jurisdiction(st, st, "state", name, _rate_entries(st, f"{v // 100}.{v % 100:02d}", (*chg, 0) if chg else None))
        if st in NO_LOCAL_TAX:
            continue
        h = _h(st + "names")
        c1, c2 = COUNTY_NAMES[h % 20], COUNTY_NAMES[(h // 20 + 1 + h % 20) % 20]
        if c2 == c1:
            c2 = COUNTY_NAMES[(h + 7) % 20]
        cities = [CITY_NAMES[(h // 7 + k * 5) % len(CITY_NAMES)] for k in range(3)]
        specs = [
            (f"{st}-C1", "county", f"{c1} County", (25, 150)),
            (f"{st}-C2", "county", f"{c2} County", (25, 150)),
            (f"{st}-CITY1", "city", f"City of {cities[0]}", (50, 200)),
            (f"{st}-CITY2", "city", f"City of {cities[1]}", (50, 150)),
            (f"{st}-CITY3", "city", f"City of {cities[2]}", (50, 200)),
            (f"{st}-TD", "district", f"{c1} Regional Transit District", (50, 100)),
            (f"{st}-SD", "district", f"{cities[2]} Stadium District", (10, 50)),
        ]
        for jid, kind, jname, (lo, hi) in specs:
            out[jid] = Jurisdiction(jid, st, kind, jname, _rate_entries(jid, _local_pct(jid, lo, hi), LOCAL_RATE_CHANGES.get(jid)))
    return out


def jurisdictions_in(state: str) -> list[Jurisdiction]:
    return [j for j in jurisdictions().values() if j.state == state]


@cache
def fingerprint() -> str:
    """Hash of every rule and rate. Changes whenever any rule data changes."""
    data = {
        "version": RULESET_VERSION,
        "states": {k: asdict(v) for k, v in state_rules().items()},
        "jurisdictions": {k: asdict(v) for k, v in jurisdictions().items()},
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]


def fmt_rate(ppm: int) -> str:
    """62500 -> '6.25%'. Keeps up to 4 decimals."""
    s = f"{ppm / 10_000:.4f}".rstrip("0").rstrip(".")
    return f"{s}%"
