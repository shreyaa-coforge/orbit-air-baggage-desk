"""Claim filing and listing rules. No function here reads the real clock."""

import csv
from datetime import datetime, timedelta

from db import CLAIM_TYPES, TIERS

DEADLINE_HOURS = {
    "gold": 4,
    "silver": 24,
    "standard": 72,
}


# Every allowed status change, in one place. A status missing from a set
# cannot be reached from that key's status. "closed" is final.
ALLOWED_CHANGES = {
    "open": {"assigned", "escalated"},
    "assigned": {"resolved", "escalated"},
    "escalated": {"assigned", "resolved"},
    "resolved": {"closed"},
    "closed": set(),
}


def _require_text(value, field):
    if value is None or not str(value).strip():
        raise ValueError(f"{field} cannot be blank")
    return str(value).strip()


def _validate_claim(passenger, flight, claim_type, tier):
    """Check new-claim input. Return cleaned (passenger, flight). Raise ValueError."""
    passenger = _require_text(passenger, "passenger")
    flight = _require_text(flight, "flight")
    if claim_type not in CLAIM_TYPES:
        raise ValueError(
            f"unknown claim type {claim_type!r}; expected one of {', '.join(CLAIM_TYPES)}"
        )
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}; expected one of {', '.join(TIERS)}")
    return passenger, flight


def file_claim(conn, passenger, flight, claim_type, tier, note, now):
    """File a new open claim and record its first history entry.

    Returns the new claim id. Raises ValueError on invalid input.
    """
    with conn:
        return _insert_claim(conn, passenger, flight, claim_type, tier, note, now)


def _insert_claim(conn, passenger, flight, claim_type, tier, note, now):
    """Validate and insert one claim plus its filing history entry.

    Does not open or commit a transaction; the caller wraps it in "with conn:".
    """
    passenger, flight = _validate_claim(passenger, flight, claim_type, tier)

    filed_at = now.isoformat()
    deadline = (now + timedelta(hours=DEADLINE_HOURS[tier])).isoformat()

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


def _load_claim(conn, claim_id):
    row = conn.execute(
        "SELECT status, deadline FROM claims WHERE id = ?", (claim_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"claim {claim_id} does not exist")
    return row


def _check_change(old_status, new_status):
    if new_status not in ALLOWED_CHANGES:
        raise ValueError(f"unknown status {new_status!r}")
    if new_status not in ALLOWED_CHANGES[old_status]:
        allowed = ", ".join(sorted(ALLOWED_CHANGES[old_status])) or "nothing (final)"
        raise ValueError(
            f"cannot change a {old_status} claim to {new_status}; allowed: {allowed}"
        )


def _apply_change(conn, claim_id, new_status, changed_by, now, agent=None):
    """Validate the change, then update the claim and record history in one transaction."""
    changed_by = _require_text(changed_by, "changed_by")
    claim = _load_claim(conn, claim_id)
    old_status = claim["status"]
    _check_change(old_status, new_status)
    if new_status == "escalated" and not is_overdue(claim["deadline"], now):
        raise ValueError(
            f"claim {claim_id} is not overdue (deadline {claim['deadline']}); "
            "only overdue claims can be escalated"
        )

    # Claim update first, then history, in one transaction: if the history
    # insert fails, the claim update is rolled back too.
    with conn:
        if agent is None:
            conn.execute(
                "UPDATE claims SET status = ? WHERE id = ?", (new_status, claim_id)
            )
        else:
            conn.execute(
                "UPDATE claims SET status = ?, assigned_agent = ? WHERE id = ?",
                (new_status, agent, claim_id),
            )
        conn.execute(
            "INSERT INTO history (claim_id, old_status, new_status, changed_by, changed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (claim_id, old_status, new_status, changed_by, now.isoformat()),
        )


def change_status(conn, claim_id, new_status, changed_by, now):
    """Move a claim to new_status if ALLOWED_CHANGES permits it.

    Raises ValueError and saves nothing if the change is not allowed.
    """
    _apply_change(conn, claim_id, new_status, changed_by, now)


def assign_claim(conn, claim_id, agent, changed_by, now):
    """Assign a claim to an agent and move it to assigned."""
    agent = _require_text(agent, "agent")
    _apply_change(conn, claim_id, "assigned", changed_by, now, agent=agent)


def resolve_claim(conn, claim_id, changed_by, now):
    """Move a claim to resolved."""
    _apply_change(conn, claim_id, "resolved", changed_by, now)


def close_claim(conn, claim_id, changed_by, now):
    """Move a claim to closed."""
    _apply_change(conn, claim_id, "closed", changed_by, now)


def get_history(conn, claim_id):
    """Return every history entry for a claim, oldest first.

    Raises ValueError if the claim does not exist.
    """
    _load_claim(conn, claim_id)
    return conn.execute(
        "SELECT id, claim_id, old_status, new_status, changed_by, changed_at "
        "FROM history WHERE claim_id = ? ORDER BY id",
        (claim_id,),
    ).fetchall()


ESCALATABLE_STATUSES = ("open", "assigned")


def is_overdue(deadline, now):
    """Return True only when now is strictly later than the deadline.

    deadline may be a datetime or an ISO timestamp string as stored in the database.
    """
    if isinstance(deadline, str):
        deadline = datetime.fromisoformat(deadline)
    return now > deadline


def escalate_claim(conn, claim_id, changed_by, now):
    """Escalate one open or assigned claim that is overdue.

    Raises ValueError and saves nothing otherwise.
    """
    status = _load_claim(conn, claim_id)["status"]
    if status not in ESCALATABLE_STATUSES:
        raise ValueError(
            f"cannot escalate a {status} claim; only open or assigned claims can be escalated"
        )
    change_status(conn, claim_id, "escalated", changed_by, now)


def escalate_overdue(conn, changed_by, now):
    """Escalate every overdue open or assigned claim. Return the escalated ids.

    Each claim is escalated in its own transaction. A database error is not
    caught: it rolls back that claim's escalation and is raised to the caller.
    """
    placeholders = ", ".join("?" for _ in ESCALATABLE_STATUSES)
    candidates = conn.execute(
        f"SELECT id, deadline FROM claims WHERE status IN ({placeholders}) ORDER BY id",
        ESCALATABLE_STATUSES,
    ).fetchall()

    escalated = []
    for row in candidates:
        if is_overdue(row["deadline"], now):
            escalate_claim(conn, row["id"], changed_by, now)
            escalated.append(row["id"])
    return escalated


SEED_COLUMNS = ("passenger", "flight", "type", "tier", "note")


def seed_claims(conn, csv_file, now):
    """File one claim per CSV row (columns passenger, flight, type, tier, note).

    csv_file is an open text file. Every row is checked with the file_claim
    rules before anything is saved, and all inserts run in one transaction,
    so a bad row or a failed insert means no rows are loaded.
    CSV contents, including notes, are stored as plain data.
    Returns the new claim ids in file order.
    """
    reader = csv.DictReader(csv_file)
    missing = [col for col in SEED_COLUMNS if col not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"CSV is missing column(s): {', '.join(missing)}")

    rows = []
    for line_no, row in enumerate(reader, start=2):
        try:
            _validate_claim(row["passenger"], row["flight"], row["type"], row["tier"])
        except ValueError as exc:
            raise ValueError(f"CSV line {line_no}: {exc}") from exc
        note = (row["note"] or "").strip() or None
        rows.append((row["passenger"], row["flight"], row["type"], row["tier"], note))

    with conn:
        return [
            _insert_claim(conn, passenger, flight, claim_type, tier, note, now)
            for passenger, flight, claim_type, tier, note in rows
        ]
