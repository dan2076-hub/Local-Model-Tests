# F03: transactional SQLite event application

Repair `main.py` using Python 3.11+ standard library only. Edit only this file. No network or external commands.

CLI: `python main.py DB EVENT_ID PAYLOAD [--crash-after-effect]`. ID and payload are nonempty strings, preserved exactly (including Unicode and whitespace). The database is initially absent or follows this schema:

```sql
CREATE TABLE events (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE effects (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
```

Implement `apply_event(connection, event_id, payload, crash_after_effect=False)`. A new event inserts exactly one matching row into each table, atomically. Repeating the same ID and payload succeeds with no additional effect. Reusing an ID with a different payload is a conflict: exit 2, nonempty stderr, both tables unchanged. Distinct IDs are independent even with the same payload. Success exits 0 with empty stdout. The function raises ValueError on invalid arguments or conflict; the CLI translates errors to exit 2 and nonempty stderr. Incorrect CLI arity/flags and empty ID/payload also exit 2 without changing an existing database. Schema creation may persist on a new empty database.

For a NEW event only, `--crash-after-effect` must call `os._exit(75)` immediately after inserting its effects row and before inserting its events row or committing either. This is a test seam for abrupt process death, not a simulated exception. On reopening, neither row for that event may remain; old committed rows survive. A later normal retry must apply it exactly once. Preserve this seam when fixing the implementation. Use the given schema, no extra user tables. SQLite auxiliary journal files are permitted. Do not modify unrelated files. Concurrent invocation is not required for this case.
