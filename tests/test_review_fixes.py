"""Regression tests for /code-review findings on the CLI and seeding."""

import sqlite3

import pytest

import db
from main import main

# Fixed times so results never depend on when the tests run.
FILED_AT = "2026-11-02T09:00:00"
AFTER_GOLD_DEADLINE_UTC = "2026-11-02T20:00:00Z"

CSV_HEADER = "passenger,flight,type,tier,note\n"


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


def claim_count(db_path):
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    finally:
        connection.close()


def assert_clean_error(capsys):
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert any(line.startswith("Error:") for line in output.splitlines()), output
    assert "Traceback" not in output


# 1. A timezone-aware --now is reported as a clear error, not a crash when it
#    is compared with the stored (timezone-free) deadlines.
def test_timezone_aware_now_is_a_clear_error(db_path, capsys):
    assert run(db_path, FILED_AT, "file", "--passenger", "Kwame Mensah",
               "--flight", "OA778", "--type", "delayed", "--tier", "gold") == 0
    capsys.readouterr()

    code = run(db_path, AFTER_GOLD_DEADLINE_UTC, "escalate")

    assert code != 0
    assert_clean_error(capsys)


# 2. Seeding is all-or-nothing: a database failure on the last row saves no rows.
def test_seed_is_all_or_nothing(db_path, tmp_path, capsys):
    connection = db.connect(db_path)
    db.init_db(connection)
    connection.execute(
        "CREATE TRIGGER fail_boom BEFORE INSERT ON claims "
        "WHEN NEW.passenger = 'Boom' "
        "BEGIN SELECT RAISE(ABORT, 'forced failure'); END"
    )
    connection.commit()
    connection.close()

    csv_path = tmp_path / "claims.csv"
    csv_path.write_text(
        CSV_HEADER
        + "Priya Raman,OA101,lost,gold,Black suitcase\n"
        + "Marcus Bell,OA204,delayed,silver,Missed connection\n"
        + "Boom,OA330,damaged,standard,Forced failure row\n",
        encoding="utf-8",
    )

    code = run(db_path, FILED_AT, "seed", str(csv_path))

    assert code != 0
    assert claim_count(db_path) == 0


# 3. A CSV saved with a UTF-8 byte-order mark seeds every row.
def test_seed_accepts_utf8_bom(db_path, tmp_path, capsys):
    csv_path = tmp_path / "claims_bom.csv"
    csv_path.write_text(
        CSV_HEADER
        + "Priya Raman,OA101,lost,gold,Black suitcase\n"
        + "Marcus Bell,OA204,delayed,silver,Missed connection\n"
        + "Elena Petrova,OA330,damaged,standard,Wheel snapped off\n",
        encoding="utf-8-sig",
    )

    code = run(db_path, FILED_AT, "seed", str(csv_path))

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert claim_count(db_path) == 3


# 4. A CSV field too large for the csv module is a clear error, not a traceback.
def test_seed_with_oversized_note_is_a_clear_error(db_path, tmp_path, capsys):
    csv_path = tmp_path / "claims_big.csv"
    huge_note = "x" * 200_001
    csv_path.write_text(
        CSV_HEADER + f"Priya Raman,OA101,lost,gold,{huge_note}\n",
        encoding="utf-8",
    )

    code = run(db_path, FILED_AT, "seed", str(csv_path))

    assert code != 0
    assert_clean_error(capsys)


# 5. list rejects an unknown tier or status instead of quietly listing nothing.
@pytest.mark.parametrize(
    "option, value",
    [("--tier", "platnum"), ("--status", "lost")],
)
def test_list_rejects_unknown_filter_values(db_path, option, value):
    code = run(db_path, FILED_AT, "list", option, value)

    assert code != 0


# 6. history for a claim that does not exist is an error.
def test_history_of_missing_claim_is_an_error(db_path, capsys):
    code = run(db_path, FILED_AT, "history", "999")

    assert code != 0
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert any(line.startswith("Error:") for line in output.splitlines()), output
