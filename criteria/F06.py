"""Private queue oracle for a confirmed-stopped candidate's separate export.

runner(argv, cwd=Path) prepends OS-isolated Python, bounds time/output/storage,
and terminates ALL candidate writers before returning. Never import candidate
modules or parse candidate SQLite databases in this host grader. Reference
fixtures and this oracle stay outside the active model workspace.
"""
import itertools
import json
from pathlib import Path
import re
import stat
from uuid import uuid4

CASE = "F06"


def _plain(path, directory=False):
    try:
        info = path.lstat()
        return not (getattr(info, "st_file_attributes", 0) & 0x400) and (
            stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
    except OSError:
        return False


def _chain(path):
    return all(_plain(p, True) for p in (path, *path.parents))


def _small(path, limit=131072):
    try:
        return _chain(path.parent) and _plain(path) and path.stat().st_size <= limit
    except OSError:
        return False


def _inventory(root, excluded):
    """Bounded metadata-only inventory; never follow candidate links."""
    if not _chain(root):
        return None
    pending, result = [root], {}
    try:
        while pending:
            directory = pending.pop()
            for path in directory.iterdir():
                if path in excluded:
                    continue
                relative = path.relative_to(root)
                if len(result) >= 256 or len(relative.parts) > 8:
                    return None
                info = path.lstat()
                if (getattr(info, "st_file_attributes", 0) & 0x400 or
                        not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))):
                    return None
                result[relative.as_posix()] = (info.st_mode, info.st_dev, info.st_ino,
                                               info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                if stat.S_ISDIR(info.st_mode):
                    pending.append(path)
        return result
    except OSError:
        return None


def _bounded(result):
    return (isinstance(getattr(result, "stdout", None), bytes)
            and isinstance(getattr(result, "stderr", None), bytes)
            and len(result.stdout) <= 65536 and len(result.stderr) <= 65536)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _json(data):
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")))
    except (ValueError, UnicodeError, RecursionError):
        return None


def _tasks(value, array=False):
    rows = value if array and isinstance(value, list) else [value] if not array else [None]
    return all(isinstance(row, dict) and set(row) == {"id", "title", "done", "tag"}
               and all(type(row[k]) is str for k in ("id", "title", "tag"))
               and type(row["done"]) is bool for row in rows)


SNAPSHOT = '''
import json, sqlite3, sys
c = sqlite3.connect(sys.argv[1], timeout=1)
c.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1048576)
c.set_progress_handler(lambda: 1, 100000)
rows = c.execute("SELECT id,title,done,tag FROM tasks ORDER BY id LIMIT 100").fetchall()
tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name LIMIT 100")]
print(json.dumps({"rows": rows, "tables": tables}))
c.close()
'''


def grade(candidate_dir, runner):
    root = Path(candidate_dir).absolute()
    checks = []
    owned_work = set()
    initial_inventory = _inventory(root, owned_work)

    def check(name, passed):
        checks.append({"id": name, "passed": bool(passed)})

    def unchanged(label):
        check(label + ":export_unchanged", initial_inventory is not None and
              _inventory(root, owned_work) == initial_inventory)

    def scenario(name, filtered=False):
        if not _chain(root):
            check(name + ":boundary", False)
            return
        work = root / (".grader-data-" + CASE + "-" + uuid4().hex)
        work.mkdir()
        owned_work.add(work)
        db = work / "queue.sqlite"
        expected = {}

        def state(label):
            safe = _chain(work) and _small(db, 2097152)
            if safe:
                try:
                    entries = list(itertools.islice(work.iterdir(), 5))
                    safe = len(entries) <= 4 and all(
                        p.name in {"queue.sqlite", "queue.sqlite-journal", "queue.sqlite-wal", "queue.sqlite-shm"}
                        and _small(p, 2097152) for p in entries)
                except OSError:
                    safe = False
            if not safe:
                check(name + ":" + label + ":state", False)
                return
            result = runner(["-I", "-c", SNAPSHOT, str(db.relative_to(root))], cwd=root)
            actual = _json(result.stdout) if _bounded(result) and result.returncode == 0 else None
            rows = [[r["id"], r["title"], int(r["done"]), r["tag"]] for r in sorted(expected.values(), key=lambda r: r["id"])]
            check(name + ":" + label + ":state", actual == {"rows": rows, "tables": ["tasks"]})

        def command(label, args, value=None, reject=False):
            if not _chain(work):
                check(name + ":" + label + ":boundary", False)
                return
            result = runner(["-m", "queuecli", "--db", str(db.relative_to(root)), *args], cwd=root)
            unchanged(name + ":" + label)
            valid = _bounded(result)
            if reject:
                valid = valid and result.returncode == 2 and not result.stdout and bool(result.stderr)
            else:
                actual = _json(result.stdout) if valid else None
                valid = valid and result.returncode == 0 and not result.stderr and _tasks(actual, isinstance(value, list)) and actual == value
            check(name + ":" + label + ":output", valid)
            state(label)

        def task(key, title, tag="", done=False):
            return dict(id=key, title=title, done=done, tag=tag)

        def add(label, key, title, tag=None):
            row = task(key, title, tag if tag is not None else "")
            expected[key] = row
            args = ["add", key, title] + (["--tag", tag] if tag is not None else [])
            command(label, args, row)

        def listing(label, args=None, predicate=lambda _: True):
            rows = [r.copy() for r in sorted(expected.values(), key=lambda r: r["id"]) if predicate(r)]
            command(label, ["list", *(args or [])], rows)

        listing("empty")
        add("unicode_add", "я2", "Корм\n雪", " Дом ")
        add("untagged_add", "A", "quote ' ?")
        add("tagged_add", "b", "same", "work")
        listing("sorted_restart")
        command("duplicate_identical", ["add", "b", "same", "--tag", "work"], reject=True)
        command("duplicate_conflict", ["add", "b", "replacement", "--tag", "different"], reject=True)
        expected["b"]["done"] = True
        command("done", ["done", "b"], expected["b"])
        command("done_again", ["done", "b"], expected["b"])
        command("unknown", ["done", "unknown"], reject=True)
        listing("restart_after_done")
        invalid = [
            [], ["add"], ["add", "", "title"], ["add", "new", ""],
            ["add", "new", "title", "--tag", ""], ["done", ""],
            ["add", "late", "title", "extra"], ["wat"],
            ["list", "--unexpected"], ["done", "b", "extra"],
        ]
        for index, args in enumerate(invalid):
            command("invalid_" + str(index), args, reject=True)
        if not filtered:
            if CASE == "F05":
                command("filter_not_baseline", ["list", "--status", "done"], reject=True)
            listing("final_preserved")
            return
        add("second_work", "q", "pending work", "work")
        add("unicode_tag", "Q", "done household", "Дом")
        expected["Q"]["done"] = True
        command("done_Q", ["done", "Q"], expected["Q"])
        add("literal_tag", "sql", "literal", "x' OR 1=1 --")
        add("spacing_tag", "space", "spaces", " work ")
        listing("default_compatible")
        listing("all", ["--status", "all"])
        listing("pending", ["--status", "pending"], lambda r: not r["done"])
        listing("done_filter", ["--status", "done"], lambda r: r["done"])
        listing("tag", ["--tag", "work"], lambda r: r["tag"] == "work")
        listing("tag_status", ["--tag", "work", "--status", "pending"], lambda r: r["tag"] == "work" and not r["done"])
        listing("status_tag", ["--status", "done", "--tag", "work"], lambda r: r["tag"] == "work" and r["done"])
        listing("unicode_exact", ["--tag", "Дом"], lambda r: r["tag"] == "Дом")
        listing("spaces_exact", ["--tag", " work "], lambda r: r["tag"] == " work ")
        listing("sql_data", ["--tag", "x' OR 1=1 --"], lambda r: r["tag"] == "x' OR 1=1 --")
        listing("no_matches", ["--tag", "absent"], lambda _: False)
        listing("case_mismatch", ["--tag", "WORK"], lambda _: False)
        listing("wildcard_is_data", ["--tag", "w%"], lambda _: False)
        invalid_filters = [
            ["list", "--status", "bad"], ["list", "--status", ""], ["list", "--status"],
            ["list", "--tag", ""], ["list", "--tag"],
            ["list", "--status", "all", "--status", "done"],
            ["list", "--tag", "work", "--tag", "Дом"],
            ["done", "b", "--status", "all"],
            ["done", "b", "--tag", "work"],
            ["add", "unexpected", "title", "--status", "pending"],
        ]
        for index, args in enumerate(invalid_filters):
            command("invalid_filter_" + str(index), args, reject=True)
        listing("final_unfiltered")

    scenario("crud")
    if CASE == "F06":
        scenario("filters", filtered=True)

    for label, argv in [("missing_db", ["-m", "queuecli", "list"]),
                        ("empty_db", ["-m", "queuecli", "--db", "", "list"])]:
        if _chain(root):
            result = runner(argv, cwd=root)
            unchanged(label)
            check(label, _bounded(result) and result.returncode == 2 and not result.stdout and bool(result.stderr))
        else:
            check(label, False)

    # Candidate-authored tests execute solely through the isolated runner.
    test_file = root / "tests" / "test_queuecli.py"
    if _small(test_file):
        result = runner(["-m", "unittest", "discover", "-s", "tests", "-p", "test_queuecli.py"], cwd=root)
        unchanged("own_unittest")
        count = re.search(rb"Ran ([0-9]{1,6}) tests?\b", result.stderr) if _bounded(result) else None
        check("own_unittest", _bounded(result) and result.returncode == 0 and count is not None and int(count[1]) >= 3)
    else:
        check("own_unittest", False)
    return {"case_id": CASE, "criteria": checks,
            "verdict": "PASS" if all(c["passed"] for c in checks) else "FAIL"}
