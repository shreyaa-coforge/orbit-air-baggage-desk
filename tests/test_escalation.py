"""Tests for deadlines and escalation (ORB-8)."""

import sqlite3
from datetime import datetime, timedelta

import pytest

import db
from claims import (
    assign_claim,
    close_claim,
    escalate_claim,
    escalate_overdue,
    file_claim,
    is_overdue,
    resolve_claim,
)

# Fixed times so results never depend on when the tests run.
FILED_AT = datetime(2026, 3, 10, 9, 0, 0)
CHANGED_AT = datetime(2026, 3, 10, 10, 0, 0)
# Gold claims filed at FILED_AT are due 4 hours later.
GOLD_DEADLINE = FILED_AT + timedelta(hours=4)
ONE_MINUTE = timedelta(minutes=1)
# Past every gold deadline, but well before any standard (72 hour) deadline.
AFTER_GOLD_DEADLINE = GOLD_DEADLINE + ONE_MINUTE


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.init_db(connection)
    yield connection
    connection.close()


def _file(conn, passenger="Ada Lovelace", flight="OA123", claim_type="lost",
          tier="gold", note=None, now=FILED_AT):
    return file_claim(conn, passenger, flight, claim_type, tier, note, now)


def _status(conn, claim_id):
    return conn.execute(
        "SELECT status FROM claims WHERE id = ?", (claim_id,)
    ).fetchone()[0]


def _history_count(conn, claim_id):
    return conn.execute(
        "SELECT COUNT(*) FROM history WHERE claim_id = ?", (claim_id,)
    ).fetchone()[0]


# 1. One minute before the deadline, the claim is not overdue.
def test_not_overdue_one_minute_before_deadline():
    assert is_overdue(GOLD_DEADLINE, GOLD_DEADLINE - ONE_MINUTE) is False


# 2. Exactly at the deadline, the claim is not overdue.
def test_not_overdue_exactly_at_deadline():
    assert is_overdue(GOLD_DEADLINE, GOLD_DEADLINE) is False


# 3. One minute after the deadline, the claim is overdue.
def test_overdue_one_minute_after_deadline():
    assert is_overdue(GOLD_DEADLINE, GOLD_DEADLINE + ONE_MINUTE) is True


# 4. escalate_overdue escalates overdue open and assigned claims and returns their ids.
def test_escalate_overdue_escalates_open_and_assigned(conn):
    open_id = _file(conn, passenger="Grace Hopper")
    assigned_id = _file(conn, passenger="Alan Turing")
    assign_claim(conn, assigned_id, "agent.kim", "supervisor.lee", CHANGED_AT)

    escalated = escalate_overdue(conn, "system", AFTER_GOLD_DEADLINE)

    assert sorted(escalated) == sorted([open_id, assigned_id])
    assert _status(conn, open_id) == "escalated"
    assert _status(conn, assigned_id) == "escalated"


# 5. escalate_overdue leaves resolved, closed, and not-yet-overdue claims alone.
def test_escalate_overdue_skips_resolved_closed_and_on_time(conn):
    resolved_id = _file(conn, passenger="Grace Hopper", tier="gold")
    assign_claim(conn, resolved_id, "agent.kim", "supervisor.lee", CHANGED_AT)
    resolve_claim(conn, resolved_id, "agent.kim", CHANGED_AT)

    closed_id = _file(conn, passenger="Alan Turing", tier="gold")
    assign_claim(conn, closed_id, "agent.kim", "supervisor.lee", CHANGED_AT)
    resolve_claim(conn, closed_id, "agent.kim", CHANGED_AT)
    close_claim(conn, closed_id, "agent.kim", CHANGED_AT)

    on_time_id = _file(conn, passenger="Ada Lovelace", tier="standard")

    counts_before = {
        claim_id: _history_count(conn, claim_id)
        for claim_id in (resolved_id, closed_id, on_time_id)
    }

    # Resolved and closed gold claims are past their deadline here;
    # the standard claim is not.
    escalated = escalate_overdue(conn, "system", AFTER_GOLD_DEADLINE)

    assert list(escalated) == []
    assert _status(conn, resolved_id) == "resolved"
    assert _status(conn, closed_id) == "closed"
    assert _status(conn, on_time_id) == "open"
    for claim_id, count in counts_before.items():
        assert _history_count(conn, claim_id) == count


# 6. Each escalation adds a history entry with old status, new status,
#    who made the change, and when.
def test_each_escalation_adds_history_entry(conn):
    open_id = _file(conn, passenger="Grace Hopper")
    assigned_id = _file(conn, passenger="Alan Turing")
    assign_claim(conn, assigned_id, "agent.kim", "supervisor.lee", CHANGED_AT)

    escalate_overdue(conn, "escalation.bot", AFTER_GOLD_DEADLINE)

    for claim_id, old_status in ((open_id, "open"), (assigned_id, "assigned")):
        row = conn.execute(
            "SELECT old_status, new_status, changed_by, changed_at "
            "FROM history WHERE claim_id = ? ORDER BY id DESC LIMIT 1",
            (claim_id,),
        ).fetchone()
        old, new, by, at = row
        assert old == old_status
        assert new == "escalated"
        assert by == "escalation.bot"
        assert datetime.fromisoformat(at) == AFTER_GOLD_DEADLINE


# 7. escalate_claim rejects a claim that is not yet overdue.
def test_escalate_claim_not_overdue_is_rejected(conn):
    claim_id = _file(conn)
    history_before = _history_count(conn, claim_id)

    with pytest.raises(ValueError):
        escalate_claim(conn, claim_id, "supervisor.lee", GOLD_DEADLINE - ONE_MINUTE)

    assert _status(conn, claim_id) == "open"
    assert _history_count(conn, claim_id) == history_before


# 8. escalate_claim rejects a resolved claim, even when it is overdue.
def test_escalate_claim_resolved_is_rejected(conn):
    claim_id = _file(conn)
    assign_claim(conn, claim_id, "agent.kim", "supervisor.lee", CHANGED_AT)
    resolve_claim(conn, claim_id, "agent.kim", CHANGED_AT)

    with pytest.raises(ValueError):
        escalate_claim(conn, claim_id, "supervisor.lee", AFTER_GOLD_DEADLINE)

    assert _status(conn, claim_id) == "resolved"


# 9. Rollback: if the history insert fails, the claim status does not change.
def test_escalation_rolls_back_when_history_insert_fails(conn):
    claim_id = _file(conn)
    history_before = _history_count(conn, claim_id)

    conn.execute(
        "CREATE TRIGGER fail_history_insert BEFORE INSERT ON history "
        "BEGIN SELECT RAISE(ABORT, 'forced failure'); END"
    )
    conn.commit()

    with pytest.raises(sqlite3.DatabaseError):
        escalate_overdue(conn, "system", AFTER_GOLD_DEADLINE)

    assert _status(conn, claim_id) == "open"
    assert _history_count(conn, claim_id) == history_before
