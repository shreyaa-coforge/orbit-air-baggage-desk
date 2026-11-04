# Plan: Orbit Air Baggage Desk

## The problem

Orbit Air tracks lost, delayed, and damaged bag claims in spreadsheets and email threads. Nobody can see at a glance which claims are close to their deadline, so claims slip past their response window and passengers wait too long. There is also no record of who changed a claim or when.

I will build a command line claims desk backed by SQLite. It files claims, assigns them to agents, moves them through a fixed set of statuses, records every status change, and escalates any claim that runs past its deadline.

## Deadline rules

- Every claim gets a deadline when it is filed: the filed time plus a set number of hours based on the passenger's tier.
  - gold: 4 hours
  - silver: 24 hours
  - standard: 72 hours
- A claim is overdue only when the current time is strictly later than its deadline. At the exact deadline minute, the claim is still on time. One minute later, it is overdue.
- No rule code reads the real clock. Every function that needs the time takes `now` as a parameter. `main.py` is the only place that picks the time: it uses the `--now` value when given, and the real clock otherwise. This keeps the tests stable and lets me demo an overdue claim on demand.

## Allowed status changes

A new claim always starts as `open`. From there, only these moves are allowed:

| From | Can move to |
|---|---|
| open | assigned, escalated |
| assigned | resolved, escalated |
| escalated | assigned, resolved |
| resolved | closed |
| closed | nothing, it is final |

- Any other move (for example, closing an open claim or resolving a closed one) is rejected with a clear error, and nothing is saved.
- Escalation has an extra condition: only `open` or `assigned` claims can be escalated, and only when they are overdue.
- The rules live in one dictionary in `claims.py`, so the full set is readable in one place and easy to test.

## Tables

### claims

| Column | Type | Notes |
|---|---|---|
| id | INTEGER | Primary key, autoincrement |
| passenger | TEXT | NOT NULL, cannot be blank |
| flight | TEXT | NOT NULL, cannot be blank |
| type | TEXT | NOT NULL, CHECK in (delayed, lost, damaged) |
| tier | TEXT | NOT NULL, CHECK in (standard, silver, gold) |
| status | TEXT | NOT NULL, default open, CHECK in (open, assigned, escalated, resolved, closed) |
| assigned_agent | TEXT | Empty until the claim is assigned |
| note | TEXT | Optional free text |
| filed_at | TEXT | ISO timestamp |
| deadline | TEXT | ISO timestamp, filed_at plus tier hours |

### history

| Column | Type | Notes |
|---|---|---|
| id | INTEGER | Primary key, autoincrement |
| claim_id | INTEGER | Foreign key to claims.id |
| old_status | TEXT | Empty for the first entry, when the claim is filed |
| new_status | TEXT | NOT NULL, CHECK in the five statuses |
| changed_by | TEXT | NOT NULL, who made the change |
| changed_at | TEXT | NOT NULL, ISO timestamp |

Every status change updates `claims` and inserts into `history` inside one transaction. If the history insert fails, the claim update rolls back too.

## Branches and planned tests

Each branch follows the same order: write the tests, commit them, run them and watch them fail, then have Claude Code write the feature code until they pass. The agent does not edit tests.

### feature/schema-and-filing (7 tests)
1. Filing a valid claim saves it with status `open` and adds a history entry from nothing to `open`.
2. A blank passenger name is rejected.
3. A blank flight number is rejected.
4. An unknown claim type is rejected.
5. An unknown tier is rejected.
6. The deadline is 4, 24, and 72 hours after filing for gold, silver, and standard.
7. Listing claims can filter by status and by tier.

### feature/assign-and-status (8 tests)
1. An open claim can be assigned to an agent, and the agent is saved.
2. An escalated claim can be assigned.
3. An assigned claim can be resolved.
4. An escalated claim can be resolved.
5. A resolved claim can be closed.
6. Closing an open claim is rejected.
7. Any change out of `closed` is rejected.
8. Every status change adds a history entry with old status, new status, who, and when.

### feature/deadlines-and-escalation (7 tests)
1. One minute before the deadline, the claim is not overdue.
2. At exactly the deadline, the claim is not overdue.
3. One minute after the deadline, the claim is overdue.
4. Escalation changes only overdue open and assigned claims, and returns their ids.
5. Escalation leaves resolved, closed, and not yet overdue claims alone.
6. Each escalation adds a history entry.
7. Rollback test: when the history insert is forced to fail, the claim status does not change.

### feature/cli (4 tests)
1. `seed` loads every row from a CSV file.
2. `--now` is passed through, so `escalate` catches a claim that is overdue at that time.
3. Invalid input, such as an unknown tier, prints a clear error instead of a traceback.
4. A claim filed through the CLI shows up in `list`.

Planned total: 26 tests, above the minimum of 20.

## Risks and how I will handle them

1. **Risk:** The agent edits or deletes a test to make it pass.
   **How I'll handle it:** Tests are committed before any feature code, CLAUDE.md says the tests are the spec, and I run `git diff tests/` after every agent session. If a test changed without a good reason, I undo it.

2. **Risk:** The fake key in `.env` ends up in Git or gets read by the agent.
   **How I'll handle it:** `.gitignore` was the first commit, a deny rule in `.claude/settings.json` blocks Claude from reading `.env`, and I add files to Git by name instead of `git add .`. Before submitting, I check the full history with `git log --all -- .env`.

3. **Risk:** Deadline tests pass today and fail tomorrow because they depend on the real clock.
   **How I'll handle it:** Rule functions take `now` as a parameter and tests use fixed timestamps, so the result never depends on when the tests run.

4. **Risk:** A status change saves to `claims` but the history entry fails, leaving the two tables out of sync.
   **How I'll handle it:** Both writes run inside one `with conn:` transaction. A dedicated test forces the history write to fail and checks that the claim did not change.

5. **Risk:** A claim note contains a hidden instruction, such as asking the agent to print `.env`.
   **How I'll handle it:** Notes are treated as plain data and never acted on. The deny rule still blocks `.env` even if the agent tries, and I record what happened in the README Hidden Instruction Check section.