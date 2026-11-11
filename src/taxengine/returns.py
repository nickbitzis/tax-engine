"""Returns preparation: liability by state and jurisdiction per filing period, refunds netted, export files, reconciliation.

Tax due on the return is recomputed from the taxable base per jurisdiction at the
correct rate for each day, summed exactly and rounded once per jurisdiction and
period. Tax collected is what the invoices charged. The difference has two parts:
  rate_lag: invoices used a rate table that had not loaded a rate change yet
  rounding: per invoice rounding vs one rounding on the return
"""

from __future__ import annotations

import csv
import io
from datetime import date

from taxengine.calc import half_up
from taxengine.rules import fmt_rate, jurisdictions, state_rules
from taxengine.sim import SimResult, months


class PeriodError(ValueError):
    pass


def quarter_of(month: str) -> str:
    y, m = month.split("-")
    return f"{y}-Q{(int(m) - 1) // 3 + 1}"


def months_in(period: str) -> list[str]:
    if "-Q" in period:
        y, q = period.split("-Q")
        return [f"{y}-{3 * (int(q) - 1) + k:02d}" for k in (1, 2, 3)]
    return [period]


def periods(state: str) -> list[str]:
    freq = state_rules()[state].filing
    out: list[str] = []
    for m in months():
        p = m if freq == "monthly" else quarter_of(m)
        if p not in out:
            out.append(p)
    return out


def due_date(period: str) -> date:
    last = months_in(period)[-1]
    y, m = (int(x) for x in last.split("-"))
    return date(y + (m == 12), m % 12 + 1, 20)


def state_return(res: SimResult, state: str, period: str) -> dict:
    rule = state_rules().get(state)
    if rule is None:
        raise PeriodError(f"unknown state {state}")
    if period not in periods(state):
        raise PeriodError(f"{state} files {rule.filing}; periods are {', '.join(periods(state))}")
    sim_months = set(months())
    ms = months_in(period)
    js = jurisdictions()
    agg = {"orders": 0, "gross": 0, "taxable": 0, "collected": 0, "refunds": 0, "refund_gross": 0, "refund_tax": 0}
    jur: dict[str, list[int]] = {}
    for m in ms:
        b = res.buckets.get((state, m))
        if not b:
            continue
        for k in agg:
            agg[k] += getattr(b, k)
        for jid, row in b.jur.items():
            t = jur.setdefault(jid, [0, 0, 0, 0])
            for i in range(4):
                t[i] += row[i]
    # refunds are already netted into the jurisdiction rows of the month they were paid
    rows = []
    for jid in sorted(jur, key=lambda j: (["state", "county", "city", "district"].index(js[j].kind), j)):
        base_x100, collected, applied_units, due_units = jur[jid]
        due = half_up(due_units)
        applied = half_up(applied_units)
        j = js[jid]
        rates = sorted({e.rate_ppm for e in j.rates if any(e.effective_from <= _end(m) for m in ms)})
        rows.append({"id": jid, "name": j.name, "kind": j.kind, "taxable": half_up(base_x100, 100),
                     "rate": " / ".join(fmt_rate(r) for r in rates[-2:]), "collected": collected, "due": due,
                     "difference": due - collected, "rate_lag": due - applied, "rounding": applied - collected})
    net_collected = agg["collected"] - agg["refund_tax"]
    due_total = sum(r["due"] for r in rows)
    return {
        "state": state, "period": period, "frequency": rule.filing, "months": ms,
        "complete": all(m in sim_months for m in ms), "due_date": due_date(period).isoformat(),
        "orders": agg["orders"], "gross_sales": agg["gross"], "refunds": agg["refunds"], "refund_sales": agg["refund_gross"],
        "net_sales": agg["gross"] - agg["refund_gross"], "taxable_sales": agg["taxable"], "exempt_sales": agg["gross"] - agg["taxable"],
        "tax_collected": agg["collected"], "refund_tax": agg["refund_tax"], "net_tax_collected": net_collected,
        "tax_due": due_total, "difference": due_total - net_collected,
        "rate_lag": sum(r["rate_lag"] for r in rows), "rounding": sum(r["rounding"] for r in rows),
        "jurisdictions": rows,
    }


def _end(month: str) -> date:
    y, m = (int(x) for x in month.split("-"))
    return date(y + (m == 12), m % 12 + 1, 1)


def summary(res: SimResult, period_month: str | None = None) -> list[dict]:
    """One row per state and filing period. With period_month, only the period that contains that month."""
    out = []
    for st, rule in state_rules().items():
        if not rule.has_tax:
            continue
        for p in periods(st):
            if period_month and period_month not in months_in(p):
                continue
            r = state_return(res, st, p)
            if r["orders"] == 0 and r["refunds"] == 0:
                continue
            out.append({k: r[k] for k in ("state", "period", "frequency", "complete", "due_date", "orders", "gross_sales", "refund_sales",
                                           "taxable_sales", "net_tax_collected", "tax_due", "difference", "rate_lag", "rounding")})
    return out


def export_csv(ret: dict) -> str:
    """The per state file a filer would key into the state portal (layout is illustrative)."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["# Larkspur Supply Co. sales tax return worksheet (illustrative rules, not current law)"])
    w.writerow(["state", "period", "frequency", "due_date", "gross_sales", "refund_sales", "net_sales", "taxable_sales", "exempt_sales",
                "tax_collected", "refund_tax", "net_tax_collected", "tax_due", "difference"])
    c = lambda v: f"{v / 100:.2f}"  # noqa: E731
    w.writerow([ret["state"], ret["period"], ret["frequency"], ret["due_date"], c(ret["gross_sales"]), c(ret["refund_sales"]), c(ret["net_sales"]),
                c(ret["taxable_sales"]), c(ret["exempt_sales"]), c(ret["tax_collected"]), c(ret["refund_tax"]), c(ret["net_tax_collected"]),
                c(ret["tax_due"]), c(ret["difference"])])
    w.writerow([])
    w.writerow(["jurisdiction_id", "name", "kind", "rate", "taxable_sales_net", "tax_collected_net", "tax_due", "difference", "rate_lag", "rounding"])
    for r in ret["jurisdictions"]:
        w.writerow([r["id"], r["name"], r["kind"], r["rate"], c(r["taxable"]), c(r["collected"]), c(r["due"]), c(r["difference"]),
                    c(r["rate_lag"]), c(r["rounding"])])
    return buf.getvalue()


def reconcile(res: SimResult) -> dict:
    """Tax collected vs tax due for every state and period, with the causes of every difference."""
    rows = summary(res)
    return {
        "periods": len(rows),
        "net_tax_collected": sum(r["net_tax_collected"] for r in rows),
        "tax_due": sum(r["tax_due"] for r in rows),
        "difference": sum(r["difference"] for r in rows),
        "rate_lag": sum(r["rate_lag"] for r in rows),
        "rounding": sum(r["rounding"] for r in rows),
        "largest": sorted(rows, key=lambda r: -abs(r["difference"]))[:8],
    }

