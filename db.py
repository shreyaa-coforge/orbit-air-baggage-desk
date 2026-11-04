"""SQLite connection and schema for the Orbit Air baggage desk."""

import sqlite3

CLAIM_TYPES = ("delayed", "lost", "damaged")
TIERS = ("standard", "silver", "gold")
STATUSES = ("open", "assigned", "escalated", "resolved", "closed")


def _in_list(values):
    return ", ".join(f"'{value}'" for value in values)


SCHEMA = f"""
CREATE TABLE IF NOT EXISTS claims (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    passenger      TEXT NOT NULL CHECK (trim(passenger) <> ''),
    flight         TEXT NOT NULL CHECK (trim(flight) <> ''),
    type           TEXT NOT NULL CHECK (type IN ({_in_list(CLAIM_TYPES)})),
    tier           TEXT NOT NULL CHECK (tier IN ({_in_list(TIERS)})),
    status         TEXT NOT NULL DEFAULT 'open'
                   CHECK (status IN ({_in_list(STATUSES)})),
    assigned_agent TEXT,
    note           TEXT,
    filed_at       TEXT NOT NULL,
    deadline       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id   INTEGER NOT NULL REFERENCES claims(id),
    old_status TEXT,
    new_status TEXT NOT NULL CHECK (new_status IN ({_in_list(STATUSES)})),
    changed_by TEXT NOT NULL,
    changed_at TEXT NOT NULL
);
"""


def connect(path):
    """Open a SQLite connection with name-addressable rows and foreign keys on."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn):
    """Create the claims and history tables if they do not exist."""
    with conn:
        conn.executescript(SCHEMA)
