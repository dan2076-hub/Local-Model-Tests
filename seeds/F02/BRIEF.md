# F02: JSON events to CSV summary

Implement `python main.py events.json summary.csv` with Python 3.11+ standard library only. Edit only `main.py`; no network or external commands.

Input is UTF-8 JSON (optional UTF-8 BOM): an array of objects, each with exactly `category` and `duration` keys. Category is a nonempty string; preserve whitespace, case, Unicode, commas, quotes and newlines exactly. Duration is an integer greater than or equal to zero: booleans, null, floats (including 1.0), strings and negative values are invalid. Empty arrays are valid. Reject malformed JSON, nonfinite numeric literals, duplicate object keys, unexpected/missing fields and all other shapes.

Write UTF-8 CSV with exactly `category,duration` as the first record, then one record per category containing the sum of its durations in decimal integer notation. Use Python Unicode string ordering for categories; support large integers without floating point conversion. Use standard CSV quoting. Either LF or CRLF record terminators are accepted. Empty input yields only the header. Repeated execution must produce identical bytes. Exit 0 on success; replace any existing output only after full validation.

Any invalid input/UTF-8, missing/unreadable input, or incorrect CLI arity must exit 2 with a nonempty error on stderr. Input bytes must never change. On failure preserve existing output byte-for-byte; do not create output if absent. Reject identical input and output paths with exit 2 and preserve bytes. Do not create unrelated files. Error wording is not prescribed.
