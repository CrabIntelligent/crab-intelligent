"""SQLite storage. One connection per call, WAL mode, explicit write transactions."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS nonces (
  nonce TEXT PRIMARY KEY, pubkey TEXT NOT NULL, role TEXT NOT NULL, message TEXT NOT NULL, expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY, pubkey TEXT NOT NULL, role TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS workers (
  pubkey TEXT PRIMARY KEY, name TEXT, families TEXT, models TEXT, first_seen REAL, last_seen REAL
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, owner TEXT NOT NULL, type TEXT NOT NULL, brief TEXT NOT NULL, public INTEGER NOT NULL,
  status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, quote TEXT, deliverable TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS steps (
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), idx INTEGER NOT NULL, key TEXT NOT NULL,
  title TEXT NOT NULL, instruction TEXT NOT NULL, web INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 1, releases INTEGER NOT NULL DEFAULT 0, output TEXT, author TEXT,
  author_family TEXT, author_model TEXT, notes TEXT, updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, step_id TEXT NOT NULL REFERENCES steps(id), job_id TEXT NOT NULL,
  status TEXT NOT NULL, exclude_family TEXT, exclude_worker TEXT, created REAL NOT NULL,
  claimed_by TEXT, claimed_family TEXT, claimed_at REAL, done_at REAL
);
CREATE TABLE IF NOT EXISTS reviews (
  id TEXT PRIMARY KEY, step_id TEXT NOT NULL, job_id TEXT NOT NULL, attempt INTEGER NOT NULL, reviewer TEXT NOT NULL,
  family TEXT NOT NULL, model TEXT, verdict TEXT NOT NULL, summary TEXT, findings TEXT, independent INTEGER NOT NULL,
  record_id INTEGER, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS records (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, day TEXT NOT NULL, kind TEXT NOT NULL,
  job_id TEXT, step_id TEXT, actor TEXT, payload TEXT NOT NULL, body TEXT NOT NULL, leaf TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS roots (
  day TEXT PRIMARY KEY, root TEXT NOT NULL, count INTEGER NOT NULL, computed REAL NOT NULL, tx TEXT
);
CREATE INDEX IF NOT EXISTS tasks_open ON tasks(status, created);
CREATE INDEX IF NOT EXISTS steps_job ON steps(job_id, idx);
CREATE INDEX IF NOT EXISTS jobs_owner ON jobs(owner, created);
CREATE INDEX IF NOT EXISTS records_day ON records(day, id);
CREATE INDEX IF NOT EXISTS reviews_created ON reviews(created);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(config.DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def read() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """A write transaction. BEGIN IMMEDIATE takes the write lock up front, so claims never race."""
    conn = connect()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def init() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.FILES_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
    finally:
        conn.close()
