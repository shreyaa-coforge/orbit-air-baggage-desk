"""Tests for the command line interface (ORB-9).

Assumed command shapes (global options come before the command):
    main.py [--db PATH] [--now ISO] file --passenger NAME --flight NO --type TYPE --tier TIER [--note TEXT]
    main.py [--db PATH] [--now ISO] list [--status STATUS] [--tier TIER]
    main.py [--db PATH] [--now ISO] assign CLAIM_ID AGENT --by NAME
    main.py [--db PATH] [--now ISO] escalate
    main.py [--db PATH] [--now ISO] history CLAIM_ID
    main.py [--db PATH] [--now ISO] seed CSV_PATH
"""

import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from main import main

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SEED_CSV = PROJECT_ROOT / "seed_claims.csv"

# Fixed times so results never depend on when the tests run.
FILED_AT = "2026-11-02T09:00:00"
AT_GOLD_DEADLINE = "2026-11-02T13:00:00"
AFTER_GOLD_DEADLINE = "2026-11-02T13:01:00"


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


def run(db_path, now, *args):
    """Call main() with the global options in front, and return its exit code.

    A SystemExit (for example from argparse) is turned into its exit code.
    """
    argv = ["--db", db_path, "--now", now, *args]
    try:
        return main(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


def file_claim(db_path, passenger, tier="standard", claim_type="lost",
               flight="OA123", now=FILED_AT):
    return run(
        db_path, now, "file",
        "--passenger", passenger,
        "--flight", flight,
        "--type", claim_type,
        "--tier", tier,
    )


def query(db_path, sql, params=()):
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(sql, params).fetchall()
    finally:
        connection.close()


def claim_id_for(db_path, passenger):
    return query(db_path, "SELECT id FROM claims WHERE passenger = ?", (passenger,))[0][0]


def mentions_id(text, claim_id):
    return re.search(rf"\b{claim_id}\b", text) is not None


# 1. seed loads every row from seed_claims.csv.
def test_seed_loads_every_row(db_path):
    code = run(db_path, FILED_AT, "seed", str(SEED_CSV))

    assert code == 0
    assert query(db_path, "SELECT COUNT(*) FROM claims")[0][0] == 12


# 2. --now is passed through, so escalate uses the given time, not the real clock.
def test_now_is_passed_through_to_escalate(db_path, capsys):
    assert file_claim(db_path, "Kwame Mensah", tier="gold") == 0
    claim_id = claim_id_for(db_path, "Kwame Mensah")
    capsys.readouterr()

    # Exactly at the 4 hour gold deadline: not overdue yet.
    assert run(db_path, AT_GOLD_DEADLINE, "escalate") == 0
    capsys.readouterr()
    assert query(db_path, "SELECT status FROM claims WHERE id = ?", (claim_id,))[0][0] == "open"

    # One minute later: overdue, so it is escalated and its id is reported.
    assert run(db_path, AFTER_GOLD_DEADLINE, "escalate") == 0
    out = capsys.readouterr().out
    assert mentions_id(out, claim_id)
    assert query(db_path, "SELECT status FROM claims WHERE id = ?", (claim_id,))[0][0] == "escalated"


# 3. Invalid input prints a clear error instead of a traceback.
def test_unknown_tier_prints_clear_error(db_path, capsys):
    code = file_claim(db_path, "Ada Lovelace", tier="platinum")

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert code != 0
    assert any(line.startswith("Error:") for line in output.splitlines())
    assert "Traceback" not in output
    assert query(db_path, "SELECT COUNT(*) FROM claims")[0][0] == 0


# 4. A claim filed through the CLI shows up in list, and the filters work.
def test_filed_claim_shows_in_list_with_filters(db_path, capsys):
    assert file_claim(db_path, "Grace Hopper", tier="gold") == 0
    assert file_claim(db_path, "Alan Turing", tier="silver") == 0
    turing_id = claim_id_for(db_path, "Alan Turing")
    assert run(db_path, FILED_AT, "assign", str(turing_id), "agent.kim",
               "--by", "supervisor.lee") == 0
    capsys.readouterr()

    assert run(db_path, FILED_AT, "list") == 0
    out = capsys.readouterr().out
    assert "Grace Hopper" in out
    assert "Alan Turing" in out

    assert run(db_path, FILED_AT, "list", "--status", "open") == 0
    out = capsys.readouterr().out
    assert "Grace Hopper" in out
    assert "Alan Turing" not in out

    assert run(db_path, FILED_AT, "list", "--tier", "silver") == 0
    out = capsys.readouterr().out
    assert "Alan Turing" in out
    assert "Grace Hopper" not in out


# 5. assign then history shows both history entries for that claim.
def test_assign_then_history_shows_both_entries(db_path, capsys):
    assert file_claim(db_path, "Ada Lovelace") == 0
    claim_id = claim_id_for(db_path, "Ada Lovelace")
    assert run(db_path, "2026-11-02T10:00:00", "assign", str(claim_id), "agent.kim",
               "--by", "supervisor.lee") == 0
    capsys.readouterr()

    assert run(db_path, "2026-11-02T11:00:00", "history", str(claim_id)) == 0
    out = capsys.readouterr().out

    # Filing entry (nothing -> open, by system) and the assignment (open -> assigned).
    assert "open" in out
    assert "assigned" in out
    assert "system" in out
    assert "supervisor.lee" in out
    assert query(db_path, "SELECT COUNT(*) FROM history WHERE claim_id = ?", (claim_id,))[0][0] == 2


# 6. main.py runs as a script and exits cleanly.
def test_script_runs_with_subprocess(db_path):
    result = subprocess.run(
        [sys.executable, "main.py", "--db", db_path, "--now", FILED_AT, "list"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
