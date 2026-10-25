"""Trusted oracle; runner(argv, cwd=Path) is the only execution boundary.

The runner prepends the configured Python executable inside its isolation.
No candidate modules are imported in the grader process. Keep this oracle
outside the model workspace; invoke only after confirmed agent stop, on a
separate exported copy, so private vectors are never visible during attempts.
"""
import csv
import io
from pathlib import Path
import stat
from uuid import uuid4


def _plain(path, directory=False):
    try:
        info = path.lstat()
        return not (getattr(info, "st_file_attributes", 0) & 0x400) and (
            stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        )
    except OSError:
        return False


def grade(candidate_dir, runner):
    root = Path(candidate_dir)
    # runner must terminate ALL candidate writers before returning. Otherwise
    # a host-side lstat/read pair cannot be a safe filesystem boundary.
    checks = []

    def check(name, passed):
        checks.append({"id": name, "passed": bool(passed)})

    def run(data, expected=None, name="valid", absent=False, same=False):
        if not _plain(root, directory=True):
            check(name + ":boundary", False)
            return None
        work = root / (".grader-data-F02-" + uuid4().hex)
        work.mkdir(exist_ok=False)
        source, output = work / "events.json", work / "summary.csv"
        if data is not None:
            source.write_bytes(data)
        if not absent:
            output.write_bytes(b"previous-output\x00")
        target = source if same else output
        result = runner(["main.py", str(source.relative_to(root)), str(target.relative_to(root))], cwd=root)
        def read_regular(path):
            if not (_plain(root, directory=True) and _plain(work, directory=True) and _plain(path)):
                return None
            try:
                return path.read_bytes()
            except OSError:
                return None

        source_bytes, output_bytes = read_regular(source), read_regular(output)
        def safely_absent(path):
            if not (_plain(root, directory=True) and _plain(work, directory=True)):
                return False
            try:
                path.lstat()
            except FileNotFoundError:
                return True
            except OSError:
                pass
            return False

        if data is not None:
            check(name + ":input_unchanged", source_bytes == data)
        else:
            check(name + ":input_absent", safely_absent(source))
        if expected is not None:
            try:
                rows = list(csv.reader(io.StringIO(output_bytes.decode("utf-8"), newline=""), strict=True)) if output_bytes is not None else None
                valid = rows == [["category", "duration"], *expected]
            except (OSError, UnicodeError, csv.Error):
                valid = False
            check(name + ":data", result.returncode == 0 and valid)
            return output_bytes
        preserved = source_bytes == data if same else (
            safely_absent(output)
            if absent else output_bytes == b"previous-output\x00"
        )
        check(name + ":reject_preserve", result.returncode == 2 and bool(result.stderr) and preserved)

    data = '\ufeff[{"category":"Я,\\\"строка\\\"\\nдва","duration":4},{"category":"beta","duration":9007199254740993},{"category":" alpha ","duration":0},{"category":"beta","duration":2},{"category":"Я,\\\"строка\\\"\\nдва","duration":3}]'.encode("utf-8")
    expected = [[" alpha ", "0"], ["beta", "9007199254740995"], ['Я,"строка"\nдва', "7"]]
    first = run(data, expected, "unicode_sum_sort")
    second = run(data, expected, "repeat")
    check("deterministic", first is not None and first == second)
    run(b"[]", [], "empty")
    invalid = {
        "bool": b'[{"category":"a","duration":true}]', "null": b'[{"category":"a","duration":null}]',
        "negative": b'[{"category":"a","duration":-1}]', "float": b'[{"category":"a","duration":1.0}]',
        "string": b'[{"category":"a","duration":"1"}]', "nonfinite": b'[{"category":"a","duration":NaN}]',
        "empty_category": b'[{"category":"","duration":0}]', "category_type": b'[{"category":1,"duration":0}]',
        "missing": b'[{"category":"a"}]', "extra": b'[{"category":"a","duration":0,"extra":1}]',
        "duplicate_key": b'[{"category":"a","duration":1,"duration":2}]', "shape": b'{}',
        "item_shape": b'[null]', "syntax": b'[', "utf8": b'\xff',
        "late_invalid": b'[{"category":"a","duration":1},{"category":"b","duration":false}]',
    }
    for name, value in invalid.items():
        run(value, name=name)
    run(invalid["bool"], name="absent_output", absent=True)
    run(b"[]", name="same_path", same=True)
    run(None, name="missing_input")
    arity = runner(["main.py"], cwd=root)
    check("arity", arity.returncode == 2 and bool(arity.stderr))
    return {"case_id": "F02", "criteria": checks, "verdict": "PASS" if all(c["passed"] for c in checks) else "FAIL"}
