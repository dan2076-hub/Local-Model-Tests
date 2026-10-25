# F01: CSV to JSON

Implement `python main.py input.csv output.json` with Python 3.11+ standard library only. Edit only `main.py`; no network or external commands.

Input is UTF-8 CSV (optional UTF-8 BOM), with exactly the header `id,title,done` in that order. CSV quoting, embedded commas/newlines, empty titles and Unicode including Cyrillic must work. Each data record has exactly three fields. `id` is a nonempty string unique within the file (no trimming or normalization). `done` is exactly `0` or `1`. A header with no data records is valid.

Write a UTF-8 JSON array of objects with exactly `id`, `title`, `done` keys. Preserve strings exactly; convert done to JSON boolean. Sort records by id using Python string ordering (Unicode code points, lexicographic, not numeric); retain deterministic bytes on repeated runs. JSON may escape Unicode; decoding it must reproduce the original strings. Exit 0 after success; replace any existing output only after complete validation.

Any invalid CSV, header, row, duplicate/empty id, invalid done, invalid UTF-8, missing/unreadable input, or incorrect CLI arity must exit 2 with a nonempty error on stderr. Leave the input byte-for-byte unchanged. On failure preserve any existing output byte-for-byte, and do not create output if it did not exist. Input and output must be distinct paths; reject the same path with exit 2 and preserve it. Do not create unrelated files. Error wording and JSON whitespace are not prescribed.
