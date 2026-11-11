"""Exemption certificates: resale, nonprofit, government. Coverage by state, expiry, validation status."""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from taxengine.rules import NO_SALES_TAX, STATES, StateRule

CERT_TYPES = ("resale", "nonprofit", "government")
STATUSES = ("valid", "pending", "rejected", "revoked")


@dataclass(frozen=True, slots=True)
class Certificate:
    id: str
    customer_id: str
    customer_name: str
    type: str
    states: tuple[str, ...]
    issued: date
    expires: date
    status: str  # valid: checked against the issuing state's registry; pending: not yet checked
    number: str

    def to_dict(self, as_of: date | None = None) -> dict:
        d = asdict(self)
        d["states"] = list(self.states)
        d["issued"], d["expires"] = self.issued.isoformat(), self.expires.isoformat()
        if as_of:
            days = (self.expires - as_of).days
            d["days_to_expiry"] = days
            d["warning"] = ("expired" if days < 0 else "expires within 60 days" if days <= 60 else None)
        return d


@dataclass(frozen=True, slots=True)
class CertCheck:
    applies: bool
    code: str
    message: str


def check(cert: Certificate | None, state: str, on: date, rule: StateRule) -> CertCheck:
    """Does this certificate exempt a sale into `state` on `on`? Every no comes with the reason."""
    if cert is None:
        return CertCheck(False, "CERT.NONE", "no exemption certificate on file")
    if cert.status != "valid":
        return CertCheck(False, "CERT.NOT_VALIDATED", f"certificate {cert.id} is {cert.status}, so the sale is taxed")
    if state not in cert.states:
        return CertCheck(False, "CERT.STATE_NOT_COVERED", f"certificate {cert.id} does not cover {state}")
    if on < cert.issued:
        return CertCheck(False, "CERT.NOT_YET_ISSUED", f"certificate {cert.id} was issued {cert.issued}, after this sale")
    if on > cert.expires:
        return CertCheck(False, "CERT.EXPIRED", f"certificate {cert.id} expired {cert.expires}")
    if cert.type not in rule.exempt_types:
        return CertCheck(False, "CERT.TYPE_NOT_ACCEPTED", f"{state} does not accept {cert.type} certificates from out of state buyers (illustrative)")
    return CertCheck(True, f"CERT.{cert.type.upper()}", f"{cert.type} certificate {cert.id} covers {state} until {cert.expires}")


ORG_A = ["Harbor", "Summit", "Bluepine", "Copperline", "Fernwood", "Granite", "Hollis", "Ironbark", "Juniper", "Kettle", "Lumen",
         "Marrow", "Northfield", "Oakhaven", "Prairie", "Quillon", "Ridgeway", "Saltmarsh", "Tidewater", "Underhill"]
ORG_B = {"resale": ["Outfitters", "Home Goods", "Trading Co.", "Supply", "Mercantile"],
         "nonprofit": ["Food Bank", "Youth League", "Literacy Project", "Animal Rescue", "Arts Council"],
         "government": ["School District", "County Public Works", "Parks Department", "Municipal Library", "Water Authority"]}


def seed_certificates(seed: int = 2026, n: int = 48) -> list[Certificate]:
    """Synthetic certificates. Some expire mid-year, some are pending or rejected, some cover only some states."""
    rng = random.Random(seed)
    taxing = [s for s in STATES if s not in NO_SALES_TAX]
    weights = [STATES[s][2] for s in taxing]
    certs = []
    for i in range(n):
        typ = CERT_TYPES[0] if i % 2 == 0 else CERT_TYPES[1 + (i // 2) % 2]
        cid = f"B{i + 1:03d}"
        name = f"{ORG_A[i % len(ORG_A)]} {ORG_B[typ][(i // 3) % 5]}"
        home = rng.choices(taxing, weights)[0]
        if typ == "resale" and i % 4 == 0:
            states = tuple(sorted(set([home, *rng.choices(taxing, weights, k=6)])))
        else:
            states = (home,)
        issued = date(2023, 1, 1) + timedelta(days=rng.randrange(0, 1000))
        life = rng.choice([365 * 3, 365 * 4, 365 * 5]) if typ != "government" else 365 * 10
        expires = issued + timedelta(days=life)
        if i % 7 == 3:  # renewals that lapse during the simulated year
            expires = date(2026, 1, 1) + timedelta(days=rng.randrange(0, 270))
        if i % 11 == 5:  # expiring soon after the end of the year
            expires = date(2026, 11, 15) + timedelta(days=rng.randrange(0, 45))
        status = "valid"
        if i % 13 == 6:
            status = "pending"
        elif i % 17 == 8:
            status = "rejected"
        certs.append(Certificate(f"EX-{1000 + i}", cid, name, typ, states, issued, expires, status, f"{home}-{rng.randrange(10**7, 10**8)}"))
    return certs
