"""Tests for assigning claims and moving them through statuses (ORB-7)."""

from datetime import datetime, timedelta

import pytest

import db
from claims import (
    assign_claim,
    change_status,
    close_claim,
    file_claim,
    get_history,
    resolve_claim,
)

# Fixed times so results never depend on when the tests run.
FILED_AT = datetime(2026, 3, 10, 9, 0, 0)
CHANGED_AT = datetime(2026, 3, 10, 10, 0, 0)
# Later than every tier's deadline, so moves into "escalated" are allowed
# even if change_status checks that the claim is overdue.
OVERDUE_AT = FILED_AT + timedelta(hours=73)


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.init_db(connection)
    yield connection
    connection.close()


def _file(conn, passenger="Ada Lovelace", flight="OA123", claim_type="lost",
          tier="standard", note=None, now=FILED_AT):
    return file_claim(conn, passenger, flight, claim_type, tier, note, now)


def _file_with_status(conn, status):
    """File a claim, then set its status directly with SQL.

    This sets up a starting status without going through the functions under test.
    """
    claim_id = _file(conn)
    if status != "open":
        conn.execute("UPDATE claims SET status = ? WHERE id = ?", (status, claim_id))
        conn.commit()
    return claim_id


def _status(conn, claim_id):
    return conn.execute(
        "SELECT status FROM claims WHERE id = ?", (claim_id,)
    ).fetchone()[0]


def _agent(conn, claim_id):
    return conn.execute(
        "SELECT assigned_agent FROM claims WHERE id = ?", (claim_id,)
    ).fetchone()[0]


def _history_count(conn, claim_id):
    return conn.execute(
        "SELECT COUNT(*) FROM history WHERE claim_id = ?", (claim_id,)
    ).fetchone()[0]


# 1. Assigning an open claim sets status assigned and saves the agent.
def test_assign_open_claim_sets_assigned_and_saves_agent(conn):
    claim_id = _file(conn)

    assign_claim(conn, claim_id, "agent.kim", "supervisor.lee", CHANGED_AT)

    assert _status(conn, claim_id) == "assigned"
    assert _agent(conn, claim_id) == "agent.kim"


# 2. Assigning an escalated claim works.
def test_assign_escalated_claim(conn):
    claim_id = _file_with_status(conn, "escalated")

    assign_claim(conn, claim_id, "agent.kim", "supervisor.lee", CHANGED_AT)

    assert _status(conn, claim_id) == "assigned"
    assert _agent(conn, claim_id) == "agent.kim"


# 3. Resolving an assigned claim works.
def test_resolve_assigned_claim(conn):
    claim_id = _file_with_status(conn, "assigned")

    resolve_claim(conn, claim_id, "agent.kim", CHANGED_AT)

    assert _status(conn, claim_id) == "resolved"


# 4. Resolving an escalated claim works.
def test_resolve_escalated_claim(conn):
    claim_id = _file_with_status(conn, "escalated")

    resolve_claim(conn, claim_id, "agent.kim", CHANGED_AT)

    assert _status(conn, claim_id) == "resolved"


# 5. Closing a resolved claim works.
def test_close_resolved_claim(conn):
    claim_id = _file_with_status(conn, "resolved")

    close_claim(conn, claim_id, "agent.kim", CHANGED_AT)

    assert _status(conn, claim_id) == "closed"


# 6. Every allowed change in the table works through change_status.
@pytest.mark.parametrize(
    "old_status, new_status",
    [
        ("open", "assigned"),
        ("open", "escalated"),
        ("assigned", "resolved"),
        ("assigned", "escalated"),
        ("escalated", "assigned"),
        ("escalated", "resolved"),
        ("resolved", "closed"),
    ],
)
def test_allowed_change_succeeds(conn, old_status, new_status):
    claim_id = _file_with_status(conn, old_status)

    change_status(conn, claim_id, new_status, "supervisor.lee", OVERDUE_AT)

    assert _status(conn, claim_id) == new_status


# 7. Disallowed changes are rejected, and nothing is saved.
@pytest.mark.parametrize(
    "old_status, new_status",
    [
        ("open", "closed"),
        ("open", "resolved"),
        ("assigned", "closed"),
        ("closed", "open"),
        ("closed", "assigned"),
    ],
)
def test_disallowed_change_is_rejected(conn, old_status, new_status):
    claim_id = _file_with_status(conn, old_status)
    history_before = _history_count(conn, claim_id)

    with pytest.raises(ValueError):
        change_status(conn, claim_id, new_status, "supervisor.lee", OVERDUE_AT)

    assert _status(conn, claim_id) == old_status
    assert _history_count(conn, claim_id) == history_before


# 8. Each status change adds a history entry with old status, new status,
#    who made the change, and when.
def test_each_change_adds_complete_history_entry(conn):
    claim_id = _file(conn)
    assign_at = CHANGED_AT
    resolve_at = CHANGED_AT + timedelta(hours=1)
    close_at = CHANGED_AT + timedelta(hours=2)

    assign_claim(conn, claim_id, "agent.kim", "supervisor.lee", assign_at)
    resolve_claim(conn, claim_id, "agent.kim", resolve_at)
    close_claim(conn, claim_id, "agent.park", close_at)

    rows = conn.execute(
        "SELECT old_status, new_status, changed_by, changed_at "
        "FROM history WHERE claim_id = ? ORDER BY id",
        (claim_id,),
    ).fetchall()
    # First entry is the filing (nothing -> open); one more per change.
    assert len(rows) == 4
    changes = [
        (old, new, by, datetime.fromisoformat(at)) for old, new, by, at in rows[1:]
    ]
    assert changes == [
        ("open", "assigned", "supervisor.lee", assign_at),
        ("assigned", "resolved", "agent.kim", resolve_at),
        ("resolved", "closed", "agent.park", close_at),
    ]

    history = get_history(conn, claim_id)
    assert len(history) == 4
    for entry in history[1:]:
        assert entry["old_status"]
        assert entry["new_status"]
        assert entry["changed_by"]
        assert entry["changed_at"]
