"""Trusted oracle. Never imports or directly executes candidate code.

grade(candidate_dir, runner): runner(argv, cwd=Path) must execute exclusively
inside the verified isolation boundary and return returncode/stdout/stderr.
Paths in argv are relative to cwd. The caller supplies the Python executable.
Keep this module outside the model workspace. Invoke only after confirmed
agent stop, against a separate exported copy; fixtures must remain private
during the attempt. This is an oracle, not an isolation implementation.
"""
import json
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
        work = root / (".grader-data-F01-" + uuid4().hex)
        work.mkdir(exist_ok=False)
        source, output = work / "input.csv", work / "output.json"
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
                content = json.loads(output_bytes.decode("utf-8")) if output_bytes is not None else None
                valid = content == expected and all(
                    set(row) == {"id", "title", "done"} and type(row["done"]) is bool
                    for row in content
                )
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                valid = False
            check(name + ":data", result.returncode == 0 and valid)
            return output_bytes
        preserved = source_bytes == data if same else (
            safely_absent(output)
            if absent else output_bytes == b"previous-output\x00"
        )
        check(name + ":reject_preserve", result.returncode == 2 and bool(result.stderr) and preserved)

    data = '\ufeffid,title,done\r\nz,"Привет, ""мир""\nвторая строка",1\r\n10,,0\r\n2, spaces ,1\r\n'.encode("utf-8")
    expected = [{"id": "10", "title": "", "done": False}, {"id": "2", "title": " spaces ", "done": True}, {"id": "z", "title": 'Привет, "мир"\nвторая строка', "done": True}]
    first = run(data, expected, "unicode_sort")
    second = run(data, expected, "repeat")
    check("deterministic", first is not None and first == second)
    run(b"id,title,done\n", [], "empty")
    invalid = {
        "bool": b"id,title,done\na,x,true\n", "duplicate": b"id,title,done\na,x,0\na,y,1\n",
        "empty_id": b"id,title,done\n,x,0\n", "extra_field": b"id,title,done\na,x,0,z\n",
        "missing_field": b"id,title,done\na,x\n", "header": b"title,id,done\nx,a,0\n",
        "unterminated_quote": b'id,title,done\na,"x,0\n', "utf8": b"id,title,done\na,\xff,0\n",
        "late_invalid": b"id,title,done\na,x,0\nb,y,2\n",
    }
    for name, value in invalid.items():
        run(value, name=name)
    run(invalid["bool"], name="absent_output", absent=True)
    run(b"id,title,done\na,x,0\n", name="same_path", same=True)
    run(None, name="missing_input")
    arity = runner(["main.py"], cwd=root)
    check("arity", arity.returncode == 2 and bool(arity.stderr))
    return {"case_id": "F01", "criteria": checks, "verdict": "PASS" if all(c["passed"] for c in checks) else "FAIL"}
