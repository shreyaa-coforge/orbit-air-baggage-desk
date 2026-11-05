"""Command line for the Orbit Air baggage desk.

This is the only module that reads the real clock: it uses --now when given,
and the current local time otherwise. All rules live in claims.py.
"""

import argparse
import csv
import sqlite3
import sys
from datetime import datetime

import claims
import db


def build_parser():
    parser = argparse.ArgumentParser(prog="main.py", description="Orbit Air baggage desk")
    parser.add_argument("--db", default="baggage.db", help="SQLite database path")
    parser.add_argument("--now", help="ISO timestamp to use instead of the real clock")
    commands = parser.add_subparsers(dest="command", required=True)

    file_cmd = commands.add_parser("file", help="file a new claim")
    file_cmd.add_argument("--passenger", required=True)
    file_cmd.add_argument("--flight", required=True)
    file_cmd.add_argument("--type", required=True, dest="claim_type")
    file_cmd.add_argument("--tier", required=True)
    file_cmd.add_argument("--note")

    list_cmd = commands.add_parser("list", help="list claims")
    list_cmd.add_argument("--status", choices=db.STATUSES)
    list_cmd.add_argument("--tier", choices=db.TIERS)

    assign_cmd = commands.add_parser("assign", help="assign a claim to an agent")
    assign_cmd.add_argument("claim_id", type=int)
    assign_cmd.add_argument("agent")
    assign_cmd.add_argument("--by", required=True, help="who made the change")

    for name, help_text in (("resolve", "resolve a claim"), ("close", "close a claim")):
        cmd = commands.add_parser(name, help=help_text)
        cmd.add_argument("claim_id", type=int)
        cmd.add_argument("--by", required=True, help="who made the change")

    escalate_cmd = commands.add_parser("escalate", help="escalate every overdue claim")
    escalate_cmd.add_argument("--by", default="system", help="who made the change")

    history_cmd = commands.add_parser("history", help="show a claim's status history")
    history_cmd.add_argument("claim_id", type=int)

    seed_cmd = commands.add_parser("seed", help="load claims from a CSV file")
    seed_cmd.add_argument("csv_path")

    return parser


def pick_now(value):
    if value is None:
        return datetime.now().replace(microsecond=0)
    try:
        now = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"--now must be an ISO timestamp, got {value!r}") from None
    if now.tzinfo is not None:
        raise ValueError(
            f"--now must be a local time without a timezone "
            f"(like 2026-11-02T09:00:00), got {value!r}"
        )
    return now


def print_claims(rows):
    if not rows:
        print("No claims found.")
        return
    for row in rows:
        agent = row["assigned_agent"] or "-"
        print(
            f"{row['id']:>4}  {row['status']:<9}  {row['tier']:<8}  {row['type']:<7}  "
            f"{row['flight']:<7}  due {row['deadline']}  agent {agent}  {row['passenger']}"
        )


def print_history(rows):
    if not rows:
        print("No history found.")
        return
    for row in rows:
        old = row["old_status"] or "(new)"
        print(f"{row['changed_at']}  {old} -> {row['new_status']}  by {row['changed_by']}")


def run_command(args, conn, now):
    if args.command == "file":
        claim_id = claims.file_claim(
            conn, args.passenger, args.flight, args.claim_type, args.tier, args.note, now
        )
        print(f"Filed claim {claim_id}.")
    elif args.command == "list":
        print_claims(claims.list_claims(conn, status=args.status, tier=args.tier))
    elif args.command == "assign":
        claims.assign_claim(conn, args.claim_id, args.agent, args.by, now)
        print(f"Claim {args.claim_id} assigned to {args.agent}.")
    elif args.command == "resolve":
        claims.resolve_claim(conn, args.claim_id, args.by, now)
        print(f"Claim {args.claim_id} resolved.")
    elif args.command == "close":
        claims.close_claim(conn, args.claim_id, args.by, now)
        print(f"Claim {args.claim_id} closed.")
    elif args.command == "escalate":
        escalated = claims.escalate_overdue(conn, args.by, now)
        if escalated:
            print("Escalated claims: " + ", ".join(str(i) for i in escalated))
        else:
            print("No overdue claims to escalate.")
    elif args.command == "history":
        print_history(claims.get_history(conn, args.claim_id))
    elif args.command == "seed":
        # utf-8-sig also accepts files saved with a byte-order mark (e.g. by Excel).
        with open(args.csv_path, newline="", encoding="utf-8-sig") as csv_file:
            ids = claims.seed_claims(conn, csv_file, now)
        print(f"Loaded {len(ids)} claims.")


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        now = pick_now(args.now)
        conn = db.connect(args.db)
        try:
            db.init_db(conn)
            run_command(args, conn, now)
        finally:
            conn.close()
    except (ValueError, sqlite3.Error, csv.Error, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
