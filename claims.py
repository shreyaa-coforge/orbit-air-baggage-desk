"""Claim filing and listing rules. No function here reads the real clock."""

from datetime import timedelta

from db import CLAIM_TYPES, TIERS

DEADLINE_HOURS = {
    "gold": 4,
    "silver": 24,
    "standard": 72,
}


def _require_text(value, field):
    if value is None or not str(value).strip():
        raise ValueError(f"{field} cannot be blank")
    return str(value).strip()


def file_claim(conn, passenger, flight, claim_type, tier, note, now):
    """File a new open claim and record its first history entry.

    Returns the new claim id. Raises ValueError on invalid input.
    """
    passenger = _require_text(passenger, "passenger")
    flight = _require_text(flight, "flight")
    if claim_type not in CLAIM_TYPES:
        raise ValueError(
            f"unknown claim type {claim_type!r}; expected one of {', '.join(CLAIM_TYPES)}"
        )
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}; expected one of {', '.join(TIERS)}")

    filed_at = now.isoformat()
    deadline = (now + timedelta(hours=DEADLINE_HOURS[tier])).isoformat()

    with conn:
        cursor = conn.execute(
            "INSERT INTO claims (passenger, flight, type, tier, status, note, filed_at, deadline) "
            "VALUES (?, ?, ?, ?, 'open', ?, ?, ?)",
            (passenger, flight, claim_type, tier, note, filed_at, deadline),
        )
        claim_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO history (claim_id, old_status, new_status, changed_by, changed_at) "
            "VALUES (?, NULL, 'open', 'system', ?)",
            (claim_id, filed_at),
        )
    return claim_id


def list_claims(conn, status=None, tier=None):
    """Return claims, optionally filtered by status and/or tier, ordered by id."""
    query = "SELECT * FROM claims"
    conditions = []
    params = []
    if status is not None:
        conditions.append("status = ?")
        params.append(status)
    if tier is not None:
        conditions.append("tier = ?")
        params.append(tier)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY id"
    return conn.execute(query, params).fetchall()
