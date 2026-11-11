"""HTTP API and web UI.

Errors share one shape: {"error": code, "message": ...}. Every cart
calculation is written to the append-only audit log with the rule versions used.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import date
from importlib import resources

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from taxengine import metrics, returns
from taxengine.calc import WAREHOUSES, CartError, Context, calculate
from taxengine.catalog import PRODUCTS
from taxengine.certs import CERT_TYPES, Certificate, check
from taxengine.golden import cart_from_dict, cases
from taxengine.logs import log, request_id, setup_logging
from taxengine.lookup import AddressError, resolve
from taxengine.nexus import alerts
from taxengine.rules import RULESET_VERSION, STATES, TAX_CODES, fingerprint, fmt_rate, jurisdictions, state_rules
from taxengine.sim import SIM_END, SIM_START, SimResult, months
from taxengine.store import Store

logger = logging.getLogger("taxengine.api")


class AddressIn(BaseModel):
    street: str = Field(default="", max_length=120)
    state: str = Field(min_length=2, max_length=2)
    zip: str = Field(min_length=5, max_length=10)


class LineInModel(BaseModel):
    sku: str
    qty: int = Field(default=1, ge=1, le=10_000)
    price: int | None = Field(default=None, ge=0, le=100_000_000)
    id: str | None = None


class CartIn(BaseModel):
    id: str | None = Field(default=None, max_length=64)
    date: date
    ship_to: AddressIn
    lines: list[LineInModel] = Field(min_length=1, max_length=200)
    shipping: int = Field(default=0, ge=0)
    handling: int = Field(default=0, ge=0)
    customer_id: str | None = None
    channel: str = Field(default="direct", pattern="^(direct|marketplace)$")
    ship_from: str | None = None
    nexus: str = Field(default="simulated", pattern="^(simulated|all)$")


class CertIn(BaseModel):
    customer_id: str = Field(min_length=1, max_length=40)
    customer_name: str = Field(min_length=2, max_length=120)
    type: str = Field(pattern="^(resale|nonprofit|government)$")
    states: list[str] = Field(min_length=1, max_length=51)
    issued: date
    expires: date
    number: str = Field(min_length=3, max_length=40)
    status: str = Field(default="pending", pattern="^(valid|pending|rejected|revoked)$")


class ApiError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        self.code, self.message, self.status = code, message, status


def create_app(sim: SimResult, store: Store) -> FastAPI:
    setup_logging()
    store.seed_certificates(sim.certificates)
    app = FastAPI(title="taxengine", version="1.0.0", description="US sales tax engine for Larkspur Supply Co. Illustrative rules, not current law.")
    regs = sim.registrations

    @app.middleware("http")
    async def observe(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = request_id.set(rid)
        request.state.rid = rid
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id.reset(token)
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        dt = time.perf_counter() - t0
        metrics.inc("taxengine_http_requests_total", route=path, status=str(response.status_code))
        metrics.observe("taxengine_http_request_seconds", dt, route=path)
        response.headers["x-request-id"] = rid
        if path not in ("/healthz", "/metrics"):
            log(logger, "request", method=request.method, route=path, status=response.status_code, ms=round(dt * 1000, 1), request_id=rid)
        return response

    @app.exception_handler(ApiError)
    async def api_error(_: Request, e: ApiError):
        return JSONResponse({"error": e.code, "message": e.message}, status_code=e.status)

    @app.exception_handler(KeyError)
    async def not_found(_: Request, e: KeyError):
        return JSONResponse({"error": "not_found", "message": f"unknown id {e.args[0] if e.args else ''}"}, status_code=404)

    def _state(st: str) -> str:
        st = st.upper()
        if st not in STATES:
            raise ApiError("unknown_state", f"unknown state {st}")
        return st

    def _ctx(nexus: str) -> Context:
        ctx = Context(registrations=None if nexus == "all" else regs)
        for c in store.certificates():
            ctx.certificates.setdefault(c.customer_id, []).append(c)
        return ctx

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return resources.files("taxengine").joinpath("static/index.html").read_text()

    @app.get("/healthz")
    def healthz() -> dict:
        store.q("SELECT 1")
        return {"status": "ok", "ruleset": RULESET_VERSION, "fingerprint": fingerprint(), "orders_simulated": sim.orders, "seed": sim.seed}

    @app.get("/metrics", response_class=PlainTextResponse)
    def prom() -> str:
        gauges = []
        counts: dict[str, int] = {}
        for ns in sim.nexus.values():
            s = ns.status(SIM_END)
            counts[s] = counts.get(s, 0) + 1
        for s, n in sorted(counts.items()):
            gauges.append(("taxengine_nexus_states", "States by nexus status at the end of the simulated year.", {"status": s}, n))
        soon = sum(1 for c in store.certificates() if 0 <= (c.expires - date.today()).days <= 60)
        gauges.append(("taxengine_certificates_expiring_60d", "Certificates expiring in the next 60 days.", {}, soon))
        gauges.append(("taxengine_audit_rows", "Rows in the calculation audit log.", {}, store.q("SELECT COUNT(*) n FROM audit")[0]["n"]))
        gauges.append(("taxengine_orders_simulated", "Orders in the simulated transaction log.", {}, sim.orders))
        return metrics.render(gauges)

    @app.get("/v1/meta")
    def meta() -> dict:
        return {"company": "Larkspur Supply Co. (synthetic)", "ruleset": RULESET_VERSION, "fingerprint": fingerprint(),
                "disclaimer": "Illustrative rates and rules, not current law. Not tax advice.",
                "period": {"start": SIM_START.isoformat(), "end": SIM_END.isoformat(), "months": months()},
                "states": [{"code": s, "name": v[0], "zips": [f"{v[1]}{k:02d}" for k in range(1, 7)]} for s, v in STATES.items()],
                "products": [{"sku": p.sku, "name": p.name, "tax_code": p.tax_code, "price": p.price} for p in PRODUCTS.values()],
                "tax_codes": TAX_CODES, "warehouses": {k: {"state": w.address.state, "opened": w.opened.isoformat()} for k, w in WAREHOUSES.items()},
                "examples": [{"id": c["id"], "why": c["why"]} for c in cases()]}

    @app.get("/v1/examples/{cid}")
    def example(cid: str) -> dict:
        for c in cases():
            if c["id"] == cid:
                return c
        raise KeyError(cid)

    @app.post("/v1/tax/calculate")
    def calc(body: CartIn, request: Request) -> dict:
        d = body.model_dump(mode="json")
        d["id"] = d["id"] or f"cart-{uuid.uuid4().hex[:8]}"
        try:
            cart = cart_from_dict(d)
            _state(cart.ship_to.state)
            r = calculate(cart, _ctx(body.nexus))
        except (CartError, AddressError, ValueError) as e:
            raise ApiError("invalid_cart", str(e)) from e
        seq = store.audit("calculate", d, r, request.state.rid)
        r["audit_seq"] = seq
        metrics.inc("taxengine_calculations_total", state=r["state"], collector=r["collector"])
        metrics.inc("taxengine_tax_calculated_cents_total", r["tax"], state=r["state"])
        log(logger, "calculated", cart_id=r["cart_id"], state=r["state"], tax=r["tax"], collector=r["collector"], audit_seq=seq,
            state_rule=r["rule_versions"]["state_rule"])
        return r

    @app.get("/v1/lookup")
    def lookup(state: str, zip: str, street: str = "", on: date | None = None) -> dict:
        st = _state(state)
        try:
            res = resolve(st, zip, street)
        except AddressError as e:
            raise ApiError("invalid_address", str(e)) from e
        on = on or SIM_END
        js = jurisdictions()

        def rows(ids):
            out = [{"id": j, "name": js[j].name, "kind": js[j].kind, "rate": fmt_rate(js[j].rate_at(on))} for j in ids]
            return out, fmt_rate(sum(js[j].rate_at(on) for j in ids))

        got, total = rows(res.jurisdictions)
        zo, zo_total = rows(res.zip_only)
        return {"state": st, "zip": res.zip, "street": street, "method": res.method, "on": on.isoformat(), "jurisdictions": got, "combined_rate": total,
                "zip_only": {"jurisdictions": zo, "combined_rate": zo_total}, "differs_from_zip_only": res.differs_from_zip_only,
                "override": res.override.note if res.override else None}

    @app.get("/v1/rules/{state}")
    def rules(state: str) -> dict:
        st = _state(state)
        r = state_rules()[st]
        d = {}
        for f in r.__dataclass_fields__:
            v = getattr(r, f)
            d[f] = v.isoformat() if isinstance(v, date) else v
        d["holidays"] = [{"name": h.name, "start": h.start.isoformat(), "end": h.end.isoformat(), "caps": h.caps, "local_too": h.local_too}
                         for h in r.holidays]
        d["jurisdictions"] = [{"id": j.id, "name": j.name, "kind": j.kind,
                               "rates": [{"effective_from": e.effective_from.isoformat(), "rate": fmt_rate(e.rate_ppm), "loaded_on": e.loaded_on.isoformat()}
                                         for e in j.rates]} for j in jurisdictions().values() if j.state == st]
        d["disclaimer"] = "Illustrative, not current law."
        return d

    @app.get("/v1/certificates")
    def certificates(customer_id: str | None = None, as_of: date | None = None) -> dict:
        as_of = as_of or date.today()
        rows = [c.to_dict(as_of) for c in store.certificates(customer_id)]
        return {"as_of": as_of.isoformat(), "certificates": rows, "count": len(rows),
                "expired": sum(1 for r in rows if r["warning"] == "expired"),
                "expiring_60d": sum(1 for r in rows if r["warning"] == "expires within 60 days")}

    @app.post("/v1/certificates", status_code=201)
    def add_certificate(body: CertIn) -> dict:
        states = tuple(sorted({_state(s) for s in body.states}))
        if body.expires <= body.issued:
            raise ApiError("invalid_certificate", "expires must be after issued")
        if body.type not in CERT_TYPES:
            raise ApiError("invalid_certificate", "unknown type")
        c = Certificate(store.next_certificate_id(), body.customer_id, body.customer_name, body.type, states, body.issued, body.expires,
                        body.status, body.number)
        store.put_certificate(c)
        metrics.inc("taxengine_certificates_created_total", type=c.type)
        log(logger, "certificate added", certificate_id=c.id, customer_id=c.customer_id, status=c.status)
        return c.to_dict(date.today())

    @app.get("/v1/certificates/{cid}/check")
    def check_certificate(cid: str, state: str, on: date) -> dict:
        c = store.certificate(cid)
        st = _state(state)
        r = check(c, st, on, state_rules()[st])
        return {"certificate_id": cid, "state": st, "on": on.isoformat(), "applies": r.applies, "code": r.code, "message": r.message}

    @app.get("/v1/nexus")
    def nexus(as_of: date | None = None) -> dict:
        as_of = as_of or SIM_END
        rows = []
        for st, ns in sim.nexus.items():
            d = ns.to_dict(as_of)
            d["name"] = STATES[st][0]
            if as_of < SIM_END:
                pts = [p for p in ns.timeline if p["month_end"] <= as_of.isoformat()]
                if pts:
                    d["trailing_sales"], d["trailing_txns"] = pts[-1]["sales"], pts[-1]["txns"]
                    d["sales_pct"] = round(100 * d["trailing_sales"] / (ns.threshold_usd * 100), 1)
                    d["txns_pct"] = round(100 * d["trailing_txns"] / ns.threshold_txns, 1) if ns.threshold_txns else None
            rows.append(d)
        order = {"crossed_pending_registration": 0, "approaching": 1, "registered": 2, "below": 3, "no_sales_tax": 4}
        rows.sort(key=lambda r: (order[r["status"]], r["crossed_on"] or "9999", r["state"]))
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        return {"as_of": as_of.isoformat(), "counts": counts, "states": rows, "alerts": alerts(sim.nexus, SIM_START),
                "crossed_in_period": sum(1 for r in rows if r["crossed_on"] and r["crossed_on"] >= SIM_START.isoformat() and r["status"] != "no_sales_tax")}

    @app.get("/v1/nexus/{state}")
    def nexus_state(state: str) -> dict:
        st = _state(state)
        ns = sim.nexus[st]
        return {**ns.to_dict(SIM_END), "name": STATES[st][0], "timeline": ns.timeline}

    @app.get("/v1/returns")
    def returns_summary(month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$")) -> dict:
        if month and month not in months():
            raise ApiError("unknown_month", f"month must be one of {months()[0]} to {months()[-1]}")
        rows = returns.summary(sim, month)
        return {"month": month, "returns": rows, "count": len(rows), "net_tax_collected": sum(r["net_tax_collected"] for r in rows),
                "tax_due": sum(r["tax_due"] for r in rows)}

    @app.get("/v1/returns/{state}/{period}")
    def state_return(state: str, period: str, format: str = Query("json", pattern="^(json|csv)$")):
        st = _state(state)
        try:
            r = returns.state_return(sim, st, period)
        except returns.PeriodError as e:
            raise ApiError("unknown_period", str(e)) from e
        if format == "csv":
            return Response(returns.export_csv(r), media_type="text/csv",
                            headers={"content-disposition": f'attachment; filename="return-{st}-{period}.csv"'})
        return r

    @app.get("/v1/reconciliation")
    def reconciliation() -> dict:
        return returns.reconcile(sim)

    @app.get("/v1/simulation")
    def simulation() -> dict:
        return {"seed": sim.seed, "scale": sim.scale, "orders": sim.orders, "by_collector": sim.by_collector}

    @app.get("/v1/audit")
    def audit(limit: int = Query(50, ge=1, le=1000)) -> dict:
        return {"rows": store.audit_rows(limit)}

    return app
