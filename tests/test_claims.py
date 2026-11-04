"""Tests for filing and listing claims (feature/schema-and-filing)."""

from datetime import datetime, timedelta

import pytest

import db
from claims import file_claim, list_claims

# Fixed time so results never depend on when the tests run.
FILED_AT = datetime(2026, 3, 10, 9, 0, 0)


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.init_db(connection)
    yield connection
    connection.close()


def _file(conn, passenger="Ada Lovelace", flight="OA123", claim_type="lost",
          tier="standard", note=None, now=FILED_AT):
    return file_claim(conn, passenger, flight, claim_type, tier, note, now)


def _claim_row(conn, claim_id):
    return conn.execute(
        "SELECT status, passenger, flight, type, tier, filed_at, deadline "
        "FROM claims WHERE id = ?",
        (claim_id,),
    ).fetchone()


# 1. A valid claim is saved as open, with a history entry from nothing to open.
def test_file_valid_claim_saves_open_and_records_history(conn):
    claim_id = _file(conn, note="Blue suitcase, red ribbon")

    status, passenger, flight, claim_type, tier, filed_at, _ = _claim_row(conn, claim_id)
    assert status == "open"
    assert passenger == "Ada Lovelace"
    assert flight == "OA123"
    assert claim_type == "lost"
    assert tier == "standard"
    assert datetime.fromisoformat(filed_at) == FILED_AT

    history = conn.execute(
        "SELECT old_status, new_status, changed_at FROM history WHERE claim_id = ?",
        (claim_id,),
    ).fetchall()
    assert len(history) == 1
    old_status, new_status, changed_at = history[0]
    assert old_status in (None, "")
    assert new_status == "open"
    assert datetime.fromisoformat(changed_at) == FILED_AT


# 2. A blank passenger name is rejected.
@pytest.mark.parametrize("passenger", ["", "   "])
def test_blank_passenger_is_rejected(conn, passenger):
    with pytest.raises(ValueError):
        _file(conn, passenger=passenger)
    assert conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0


# 3. A blank flight number is rejected.
@pytest.mark.parametrize("flight", ["", "   "])
def test_blank_flight_is_rejected(conn, flight):
    with pytest.raises(ValueError):
        _file(conn, flight=flight)
    assert conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0


# 4. An unknown claim type is rejected.
def test_unknown_claim_type_is_rejected(conn):
    with pytest.raises(ValueError):
        _file(conn, claim_type="stolen")
    assert conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0


# 5. An unknown tier is rejected.
def test_unknown_tier_is_rejected(conn):
    with pytest.raises(ValueError):
        _file(conn, tier="platinum")
    assert conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0


# 6. Deadline is filed time plus 4 / 24 / 72 hours by tier.
@pytest.mark.parametrize(
    "tier, hours",
    [("gold", 4), ("silver", 24), ("standard", 72)],
)
def test_deadline_depends_on_tier(conn, tier, hours):
    claim_id = _file(conn, tier=tier)
    deadline = _claim_row(conn, claim_id)[6]
    assert datetime.fromisoformat(deadline) == FILED_AT + timedelta(hours=hours)


# 7. Listing claims can filter by status and by tier.
def test_list_claims_filters_by_status_and_tier(conn):
    gold_id = _file(conn, passenger="Grace Hopper", tier="gold")
    silver_id = _file(conn, passenger="Alan Turing", tier="silver")
    standard_id = _file(conn, passenger="Ada Lovelace", tier="standard")

    # Move one claim off "open" directly so the status filter has something to exclude.
    conn.execute("UPDATE claims SET status = 'assigned' WHERE id = ?", (silver_id,))
    conn.commit()

    def ids(rows):
        return sorted(row["id"] for row in rows)

    assert ids(list_claims(conn)) == sorted([gold_id, silver_id, standard_id])
    assert ids(list_claims(conn, status="open")) == sorted([gold_id, standard_id])
    assert ids(list_claims(conn, status="assigned")) == [silver_id]
    assert ids(list_claims(conn, tier="gold")) == [gold_id]
    assert ids(list_claims(conn, status="open", tier="silver")) == []
    assert ids(list_claims(conn, status="assigned", tier="silver")) == [silver_id]
