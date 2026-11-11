"""Command line: run the API, explain a cart, look up an address, nexus, returns, reconciliation, and the golden cart gate."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

DEFAULT_DB = os.environ.get("TAXENGINE_DB_PATH", "data/taxengine.db")
DEFAULT_CACHE = os.environ.get("TAXENGINE_CACHE_DIR", "data/cache")
DEFAULT_GOLDEN = "golden/carts.json"


def _sim(args):
    from taxengine.sim import load_or_run

    return load_or_run(args.seed, args.scale, args.cache or None)


def _money(c: int) -> str:
    return f"{'-' if c < 0 else ''}${abs(c) / 100:,.2f}"


def main(argv: list[str] | None = None) -> int:
    from taxengine.sim import DEFAULT_SEED

    ap = argparse.ArgumentParser(prog="taxengine", description="US sales tax engine (illustrative rules, not current law)")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--scale", type=float, default=float(os.environ.get("TAXENGINE_SCALE", "1.0")), help="share of the ~200,000 orders to simulate")
    ap.add_argument("--cache", default=DEFAULT_CACHE, help="simulation cache directory ('' to disable)")
    ap.add_argument("--db", default=DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("api")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8877)
    p = sub.add_parser("explain", help="explain a golden cart by id, or a cart JSON file")
    p.add_argument("cart")
    p.add_argument("--nexus", choices=["all", "simulated"], default="all")
    p = sub.add_parser("lookup")
    p.add_argument("state")
    p.add_argument("zip")
    p.add_argument("street", nargs="?", default="")
    sub.add_parser("simulate")
    p = sub.add_parser("nexus")
    p.add_argument("--all", action="store_true", help="include states that crossed before the period")
    p = sub.add_parser("returns")
    p.add_argument("--month", default=None)
    p = sub.add_parser("export")
    p.add_argument("state")
    p.add_argument("period")
    p.add_argument("--out", default=None)
    sub.add_parser("reconcile")
    p = sub.add_parser("gate")
    p.add_argument("--golden", default=DEFAULT_GOLDEN)
    p = sub.add_parser("golden")
    p.add_argument("--out", default=DEFAULT_GOLDEN)
    p = sub.add_parser("summary")
    p.add_argument("--out", default="docs/summary.json")
    p = sub.add_parser("healthcheck")
    p.add_argument("--url", default="http://127.0.0.1:8877/healthz")
    a = ap.parse_args(argv)

    if a.cmd == "healthcheck":
        try:
            with urllib.request.urlopen(a.url, timeout=4) as r:
                return 0 if r.status == 200 else 1
        except Exception:
            return 1

    if a.cmd == "gate":
        from taxengine.golden import gate

        ok, problems, n = gate(a.golden)
        for line in problems:
            print(line)
        print(f"golden carts: {n} checked, {'all match' if ok else f'{len(problems)} differ'}")
        return 0 if ok else 1

    if a.cmd == "golden":
        from taxengine.golden import write

        print(f"wrote {write(a.out)} carts to {a.out}")
        return 0

    if a.cmd == "lookup":
        from taxengine.lookup import resolve
        from taxengine.rules import fmt_rate, jurisdictions
        from taxengine.sim import SIM_END

        js = jurisdictions()
        r = resolve(a.state.upper(), a.zip, a.street)
        show = lambda ids: ", ".join(f"{js[j].name} {fmt_rate(js[j].rate_at(SIM_END))}" for j in ids)  # noqa: E731
        tot = lambda ids: fmt_rate(sum(js[j].rate_at(SIM_END) for j in ids))  # noqa: E731
        print(f"{a.state.upper()} {r.zip} {a.street!r}: method {r.method}")
        print(f"  street level: {show(r.jurisdictions)} = {tot(r.jurisdictions)}")
        print(f"  ZIP only:     {show(r.zip_only)} = {tot(r.zip_only)}")
        if r.override:
            print(f"  override: {r.override.note}")
        return 0

    if a.cmd == "explain":
        from taxengine.calc import Context, calculate, explain_text
        from taxengine.golden import cart_from_dict, cases, golden_context

        if Path(a.cart).exists():
            d = json.loads(Path(a.cart).read_text())
        else:
            d = next((c for c in cases() if c["id"] == a.cart), None)
            if d is None:
                print(f"no golden cart {a.cart} and no file by that name", file=sys.stderr)
                return 2
        ctx = golden_context()
        if a.nexus == "simulated":
            ctx = Context(registrations=_sim(a).registrations, certificates=ctx.certificates)
        print(explain_text(calculate(cart_from_dict(d), ctx)))
        return 0

    if a.cmd == "api":
        import uvicorn

        from taxengine.api import create_app
        from taxengine.logs import setup_logging
        from taxengine.store import Store

        setup_logging()
        sim = _sim(a)
        uvicorn.run(create_app(sim, Store(a.db)), host=a.host, port=a.port, log_level="warning")
        return 0

    sim = _sim(a)
    from taxengine import returns
    from taxengine.sim import SIM_END, SIM_START

    if a.cmd == "simulate":
        print(f"seed {sim.seed}, scale {sim.scale:g}: {sim.orders:,} orders in {sim.seconds:.1f} s (or loaded from cache)")
        for k, (n, sub_, tax) in sorted(sim.by_collector.items()):
            print(f"  {k:<24} {n:>8,} orders  sales {_money(sub_):>16}  tax {_money(tax):>14}")
        return 0
    if a.cmd == "nexus":
        print(f"{'state':<6}{'status':<30}{'threshold':<22}{'trailing sales':>16}{'txns':>8}  crossed     by            collect from")
        for st, ns in sorted(sim.nexus.items(), key=lambda kv: (str(kv[1].crossed_on), kv[0])):
            if not a.all and ns.crossed_on and ns.crossed_on < SIM_START:
                continue
            thr = f"${ns.threshold_usd:,}" + (f" {ns.mode} {ns.threshold_txns} txns" if ns.threshold_txns else "")
            print(f"{st:<6}{ns.status(SIM_END):<30}{thr:<22}{_money(ns.trailing_sales):>16}{ns.trailing_txns:>8}  "
                  f"{ns.crossed_on or '-'!s:<11} {ns.crossed_by or '-':<13} {ns.collection_start or '-'}")
        return 0
    if a.cmd == "returns":
        rows = returns.summary(sim, a.month)
        for r in rows:
            print(f"{r['state']} {r['period']:<8} {r['frequency']:<9} orders {r['orders']:>6}  net collected {_money(r['net_tax_collected']):>12}  "
                  f"due {_money(r['tax_due']):>12}  diff {_money(r['difference']):>8}")
        return 0
    if a.cmd == "export":
        csv_text = returns.export_csv(returns.state_return(sim, a.state.upper(), a.period))
        if a.out:
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_text(csv_text)
            print(f"wrote {a.out}")
        else:
            print(csv_text, end="")
        return 0
    if a.cmd == "reconcile":
        rec = returns.reconcile(sim)
        print(f"{rec['periods']} state periods: net collected {_money(rec['net_tax_collected'])}, due {_money(rec['tax_due'])}, "
              f"difference {_money(rec['difference'])} (rate table lag {_money(rec['rate_lag'])}, rounding {_money(rec['rounding'])})")
        for r in rec["largest"]:
            print(f"  {r['state']} {r['period']:<8} diff {_money(r['difference']):>8}  lag {_money(r['rate_lag']):>8}  rounding {_money(r['rounding']):>8}")
        return 0
    if a.cmd == "summary":
        from taxengine.nexus import alerts

        rec = returns.reconcile(sim)
        data = {
            "seed": sim.seed, "scale": sim.scale, "orders": sim.orders, "by_collector": sim.by_collector,
            "nexus": {st: ns.to_dict(SIM_END) for st, ns in sorted(sim.nexus.items())},
            "alerts": alerts(sim.nexus, SIM_START),
            "reconciliation": {k: v for k, v in rec.items() if k != "largest"},
        }
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(data, indent=1) + "\n")
        print(f"wrote {a.out}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
