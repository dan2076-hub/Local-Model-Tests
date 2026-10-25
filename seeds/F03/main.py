"""Repair the transaction and duplicate handling described in BRIEF.md."""
import os
import sqlite3
import sys


def apply_event(connection, event_id, payload, crash_after_effect=False):
    # Broken: commits half an event and silently ignores conflicting payloads.
    connection.execute("INSERT OR IGNORE INTO effects VALUES (?, ?)", (event_id, payload))
    connection.commit()
    if crash_after_effect:
        os._exit(75)
    connection.execute("INSERT OR IGNORE INTO events VALUES (?, ?)", (event_id, payload))
    connection.commit()


def main():
    if len(sys.argv) not in (4, 5) or (len(sys.argv) == 5 and sys.argv[4] != "--crash-after-effect"):
        raise ValueError("usage: main.py DB EVENT_ID PAYLOAD [--crash-after-effect]")
    with sqlite3.connect(sys.argv[1]) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS effects (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        apply_event(connection, sys.argv[2], sys.argv[3], len(sys.argv) == 5)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
