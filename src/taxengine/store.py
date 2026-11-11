"""SQLite storage: exemption certificates and the calculation audit log.

The audit log is append-only (triggers block UPDATE and DELETE). Every API
calculation writes one row with the request, the result totals, and the rule
versions used, so any invoice can be explained later with the rules of that day.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, date, datetime
from pathlib import Path

from taxengine.certs import Certificate

SCHEMA = """
CREATE TABLE IF NOT EXISTS certificates (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    customer_name TEXT NOT NULL,
    type TEXT NOT NULL,
    states TEXT NOT NULL,
    issued TEXT NOT NULL,
    expires TEXT NOT NULL,
    status TEXT NOT NULL,
    number TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS certificates_customer ON certificates (customer_id);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    cart_id TEXT NOT NULL,
    state TEXT NOT NULL,
    tax INTEGER NOT NULL,
    ruleset TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    state_rule TEXT NOT NULL,
    rates TEXT NOT NULL,
    request TEXT NOT NULL,
    request_id TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
"""


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript(SCHEMA)

    def q(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def x(self, sql: str, args: tuple = ()) -> None:
        with self.lock:
            self.db.execute(sql, args)

    # certificates
    def put_certificate(self, c: Certificate) -> None:
        self.x("INSERT OR REPLACE INTO certificates VALUES (?,?,?,?,?,?,?,?,?)",
               (c.id, c.customer_id, c.customer_name, c.type, ",".join(c.states), c.issued.isoformat(), c.expires.isoformat(), c.status, c.number))

    def seed_certificates(self, certs: list[Certificate]) -> None:
        if not self.q("SELECT 1 FROM certificates LIMIT 1"):
            for c in certs:
                self.put_certificate(c)

    @staticmethod
    def _cert(r: dict) -> Certificate:
        return Certificate(r["id"], r["customer_id"], r["customer_name"], r["type"], tuple(r["states"].split(",")),
                           date.fromisoformat(r["issued"]), date.fromisoformat(r["expires"]), r["status"], r["number"])

    def certificates(self, customer_id: str | None = None) -> list[Certificate]:
        if customer_id:
            rows = self.q("SELECT * FROM certificates WHERE customer_id = ? ORDER BY id", (customer_id,))
        else:
            rows = self.q("SELECT * FROM certificates ORDER BY id")
        return [self._cert(r) for r in rows]

    def certificate(self, cid: str) -> Certificate:
        rows = self.q("SELECT * FROM certificates WHERE id = ?", (cid,))
        if not rows:
            raise KeyError(cid)
        return self._cert(rows[0])

    def next_certificate_id(self) -> str:
        n = self.q("SELECT COUNT(*) n FROM certificates")[0]["n"]
        return f"EX-{2000 + n}"

    # audit
    def audit(self, kind: str, request: dict, result: dict, request_id: str) -> int:
        v = result["rule_versions"]
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO audit (ts, kind, cart_id, state, tax, ruleset, fingerprint, state_rule, rates, request, request_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (datetime.now(UTC).isoformat(timespec="milliseconds"), kind, result["cart_id"], result["state"], result["tax"], v["ruleset"],
                 v["fingerprint"], v["state_rule"], json.dumps(v["rates"]), json.dumps(request, default=str), request_id))
            return int(cur.lastrowid)

    def audit_rows(self, limit: int = 100) -> list[dict]:
        rows = self.q("SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (limit,))
        for r in rows:
            r["rates"] = json.loads(r["rates"])
            r["request"] = json.loads(r["request"])
        return rows
