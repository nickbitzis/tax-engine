"""Economic nexus tracking: trailing 12 month sales and transaction counts per state against illustrative thresholds.

Input is daily totals per state. The window for a day d is the 365 days ending on d.
A state is crossed on the first day the window meets its threshold (sales, or
transactions, or both for "and" states). Marketplace sales count only where the
state says so. The registration (collection start) date follows the state's rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from taxengine.calc import WAREHOUSES
from taxengine.rules import StateRule, state_rules

WINDOW = 365
APPROACH = 0.8


def registration_date(crossed: date, rule: str) -> date:
    if rule == "30_days":
        return crossed + timedelta(days=30)
    if rule == "next_quarter":
        q = (crossed.month - 1) // 3 + 1
        return date(crossed.year + 1, 1, 1) if q == 4 else date(crossed.year, 3 * q + 1, 1)
    return date(crossed.year + 1, 1, 1) if crossed.month == 12 else date(crossed.year, crossed.month + 1, 1)


def physical_nexus() -> dict[str, tuple[date, str]]:
    out: dict[str, tuple[date, str]] = {}
    for w in WAREHOUSES.values():
        st = w.address.state
        if st not in out or w.opened < out[st][0]:
            out[st] = (w.opened, f"physical: warehouse {w.id} opened {w.opened}")
    return out


@dataclass
class StateNexus:
    state: str
    has_tax: bool
    threshold_usd: int
    threshold_txns: int | None
    mode: str
    crossed_on: date | None = None
    crossed_by: str | None = None
    approached_on: date | None = None
    registration_date: date | None = None
    collection_start: date | None = None
    reason: str | None = None
    trailing_sales: int = 0  # cents, as of the tracker's end date
    trailing_txns: int = 0
    timeline: list[dict] = field(default_factory=list)  # month end points

    def progress(self) -> dict:
        s = self.trailing_sales / (self.threshold_usd * 100)
        t = self.trailing_txns / self.threshold_txns if self.threshold_txns else None
        return {"sales_pct": round(100 * s, 1), "txns_pct": None if t is None else round(100 * t, 1)}

    def status(self, as_of: date) -> str:
        if not self.has_tax:
            return "no_sales_tax"
        if self.collection_start and self.collection_start <= as_of:
            return "registered"
        if self.crossed_on and self.crossed_on <= as_of:
            return "crossed_pending_registration"
        if self.approached_on and self.approached_on <= as_of:
            return "approaching"
        return "below"

    def to_dict(self, as_of: date) -> dict:
        iso = lambda d: d.isoformat() if d else None  # noqa: E731
        return {"state": self.state, "status": self.status(as_of), "threshold_usd": self.threshold_usd, "threshold_txns": self.threshold_txns,
                "threshold_mode": self.mode, "trailing_sales": self.trailing_sales, "trailing_txns": self.trailing_txns, **self.progress(),
                "crossed_on": iso(self.crossed_on), "crossed_by": self.crossed_by, "approached_on": iso(self.approached_on),
                "registration_date": iso(self.registration_date), "collection_start": iso(self.collection_start), "reason": self.reason}


def _met(rule: StateRule, sales: int, txns: int) -> tuple[bool, str | None]:
    s_ok = sales >= rule.threshold_usd * 100
    t_ok = rule.threshold_txns is not None and txns >= rule.threshold_txns
    if rule.threshold_mode == "and":
        return (s_ok and t_ok), ("sales and transactions" if s_ok and t_ok else None)
    if s_ok:
        return True, "sales"
    if t_ok:
        return True, "transactions"
    return False, None


def _near(rule: StateRule, sales: int, txns: int) -> bool:
    s = sales >= APPROACH * rule.threshold_usd * 100
    t = rule.threshold_txns is not None and txns >= APPROACH * rule.threshold_txns
    return (s and t) if rule.threshold_mode == "and" else (s or t)


def track(daily: dict[str, list[list[int]]], start: date, end: date | None = None) -> dict[str, StateNexus]:
    """daily[state][i] = [sales_cents, txns, marketplace_sales_cents, marketplace_txns] for day start + i."""
    rules = state_rules()
    phys = physical_nexus()
    out: dict[str, StateNexus] = {}
    for st, rows in daily.items():
        rule = rules[st]
        n = len(rows) if end is None else min(len(rows), (end - start).days + 1)
        ns = StateNexus(st, rule.has_tax, rule.threshold_usd, rule.threshold_txns, rule.threshold_mode)
        ws = wt = 0
        for i in range(n):
            r = rows[i]
            ds = r[0] - (0 if rule.count_marketplace else r[2])
            dt = r[1] - (0 if rule.count_marketplace else r[3])
            ws += ds
            wt += dt
            if i >= WINDOW:
                o = rows[i - WINDOW]
                ws -= o[0] - (0 if rule.count_marketplace else o[2])
                wt -= o[1] - (0 if rule.count_marketplace else o[3])
            d = start + timedelta(days=i)
            if ns.approached_on is None and _near(rule, ws, wt):
                ns.approached_on = d
            if ns.crossed_on is None:
                ok, by = _met(rule, ws, wt)
                if ok:
                    ns.crossed_on, ns.crossed_by = d, by
            nd = d + timedelta(days=1)
            if nd.day == 1:
                ns.timeline.append({"month_end": d.isoformat(), "sales": ws, "txns": wt})
        ns.trailing_sales, ns.trailing_txns = ws, wt
        if rule.has_tax:
            if ns.crossed_on:
                ns.registration_date = registration_date(ns.crossed_on, rule.registration)
                ns.collection_start, ns.reason = ns.registration_date, f"economic: crossed by {ns.crossed_by} on {ns.crossed_on}"
            if st in phys and (ns.collection_start is None or phys[st][0] < ns.collection_start):
                ns.collection_start, ns.reason = phys[st]
        out[st] = ns
    return out


def registrations(tracked: dict[str, StateNexus]) -> dict[str, date]:
    return {st: ns.collection_start for st, ns in tracked.items() if ns.collection_start}


def alerts(tracked: dict[str, StateNexus], since: date) -> list[dict]:
    """Threshold events on or after `since`, oldest first."""
    out = []
    for st, ns in tracked.items():
        if not ns.has_tax:
            continue
        if ns.approached_on and ns.approached_on >= since:
            out.append({"date": ns.approached_on.isoformat(), "state": st, "kind": "approaching",
                        "message": f"{st} reached {int(APPROACH * 100)}% of its threshold"})
        if ns.crossed_on and ns.crossed_on >= since:
            out.append({"date": ns.crossed_on.isoformat(), "state": st, "kind": "crossed",
                        "message": f"{st} crossed its threshold by {ns.crossed_by}; register and collect from {ns.registration_date}"})
    return sorted(out, key=lambda a: (a["date"], a["state"]))
