"""Storage contract and SQLite implementation.

Backends must implement atomic revisions, inbox deduplication, leases, and quota
reservations. No caller relies on SQL or a shared filesystem: a cloud backend
can implement these operations with conditional writes and managed queues.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol


class Storage(Protocol):
    def get(self, key: str) -> dict | None: ...
    def put(self, key: str, value: dict) -> int: ...
    def scan(self, prefix: str) -> dict: ...
    def enqueue(self, digest: str, account: str, payload: dict, now: float) -> bool: ...
    def pending(self, limit: int = 100) -> list: ...
    def acknowledge(self, digest: str) -> None: ...
    def lease(self, name: str, owner: str, now: float, seconds: float) -> bool: ...
    def release(self, name: str, owner: str) -> None: ...
    def reserve(self, account: str, now: float, limit: int, window: int) -> bool: ...


class SQLiteStorage:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("Unsupported WeatherProxy database schema")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS inbox (
                    digest TEXT PRIMARY KEY, account TEXT NOT NULL,
                    payload TEXT, received REAL NOT NULL, processed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS leases (
                    name TEXT PRIMARY KEY, owner TEXT NOT NULL, until REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS budgets (
                    account TEXT NOT NULL, window INTEGER NOT NULL,
                    start REAL NOT NULL, used INTEGER NOT NULL,
                    PRIMARY KEY(account, window));
                PRAGMA user_version=1;
            """)
        os.chmod(self.path, 0o600)

    @contextmanager
    def connection(self):
        # Leave headroom under Netatmo's five-second callback deadline.
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key):
        with self.connection() as db:
            row = db.execute("SELECT value, revision FROM documents WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        return {"value": json.loads(row["value"]), "revision": row["revision"]}

    def put(self, key, value):
        encoded = json.dumps(value, allow_nan=False)
        with self.connection() as db:
            row = db.execute("""INSERT INTO documents(key,value) VALUES (?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, revision=revision+1
                RETURNING revision""", (key, encoded)).fetchone()
            return row[0]

    def scan(self, prefix):
        with self.connection() as db:
            rows = db.execute("SELECT key,value,revision FROM documents WHERE key>=? AND key<?",
                              (prefix, prefix + "\U0010ffff")).fetchall()
        return {r["key"]: {"value": json.loads(r["value"]), "revision": r["revision"]}
                for r in rows if r["key"].startswith(prefix)}

    def enqueue(self, digest, account, payload, now):
        with self.connection() as db:
            return db.execute("INSERT OR IGNORE INTO inbox(digest,account,payload,received) VALUES (?,?,?,?)",
                              (digest, account, json.dumps(payload, allow_nan=False), now)).rowcount == 1

    def pending(self, limit=100):
        with self.connection() as db:
            rows = db.execute("SELECT * FROM inbox WHERE processed=0 ORDER BY received LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def acknowledge(self, digest):
        with self.connection() as db:
            # Keep the digest for replay suppression; discard event/user details.
            db.execute("UPDATE inbox SET processed=1,payload=NULL WHERE digest=?", (digest,))

    def lease(self, name, owner, now, seconds):
        with self.connection() as db:
            return db.execute("""INSERT INTO leases VALUES (?,?,?)
                ON CONFLICT(name) DO UPDATE SET owner=excluded.owner, until=excluded.until
                WHERE leases.until<=? OR leases.owner=?""",
                (name, owner, now + seconds, now, owner)).rowcount == 1

    def release(self, name, owner):
        with self.connection() as db:
            db.execute("DELETE FROM leases WHERE name=? AND owner=?", (name, owner))

    def reserve(self, account, now, limit, window):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT start,used FROM budgets WHERE account=? AND window=?", (account, window)).fetchone()
            start, used = (row[0], row[1]) if row and now < row[0] + window else (now, 0)
            if used >= limit:
                return False
            db.execute("INSERT OR REPLACE INTO budgets VALUES (?,?,?,?)", (account, window, start, used + 1))
        return True


def open_storage(config):
    backend = config.get("backend", "sqlite")
    if backend != "sqlite":
        raise ValueError(f"Unsupported storage backend: {backend}")
    return SQLiteStorage(config.get("path", "data/weather-proxy.sqlite3"))
