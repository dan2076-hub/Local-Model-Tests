# F05: build a persistent queue CLI

Implement the prepared package skeleton and its tests.

Use Python 3.11+ standard library only. No network or external commands in the application. Edit only `queuecli/__init__.py`, `queuecli/__main__.py`, `queuecli/store.py`, and `tests/test_queuecli.py`. Keep a multi-file package with CLI handling in `__main__.py` and SQLite operations in `store.py`.

CLI: `python -m queuecli --db DB add ID TITLE [--tag TAG]`, `python -m queuecli --db DB list`, `python -m queuecli --db DB done ID`. The global `--db` argument is required and precedes the subcommand. ID/title are nonempty strings; whitespace, case and Unicode are preserved. The optional add tag is a nonempty string when supplied; omitted tag is the empty string. Arguments beginning with "-" are outside the data domain. Reject unknown commands/options, missing/extra arguments and empty ID/title/supplied tag with exit 2, empty stdout and nonempty stderr.

Database starts absent or has exactly this user table:
```sql
CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, done INTEGER NOT NULL CHECK(done IN (0,1)), tag TEXT NOT NULL);
```
Persist each successful mutation transactionally. An add creates a pending task; ANY duplicate ID, even identical data, is an error with no mutation. Done marks an existing ID done and is idempotent if already done; unknown ID is an error with no mutation. Different invocations must see earlier committed changes. List on an empty/missing database returns an empty array (creating the empty schema is allowed). Invalid input and rejected operations preserve existing logical rows. No extra user tables or unrelated files. Concurrent invocation is outside this case.

Successful add/done prints one JSON object, list prints a JSON array sorted by Python Unicode ID order. Each task has exactly `id`, `title`, `done`, `tag`; done is a JSON boolean, all other fields are strings. UTF-8 output, JSON escaping/key order/whitespace are flexible. Success exits 0 with empty stderr. Errors exit 2 with empty stdout and nonempty stderr. No debug output.

Write at least three passing standard-library unittest test methods in `tests/test_queuecli.py`, covering persistence across reopen, duplicate rejection/preservation, and done/unknown behavior. They must run with `python -m unittest discover -s tests -p test_queuecli.py`, use only temporary databases, and need no installed dependencies. Tests are part of the deliverable; reference implementations and hidden test vectors are not supplied.
