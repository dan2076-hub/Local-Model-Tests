"""Private oracle, only for a stopped candidate's separate export.

runner(argv, cwd=Path) prepends isolated Python, bounds time/output, and
terminates ALL writers before returning. Even SQLite inspection executes
through that boundary; no candidate imports or untrusted DB parsing here.
"""
import json
from pathlib import Path
import stat
from uuid import uuid4


def _plain(path, directory=False):
    try:
        info = path.lstat()
        return not (getattr(info, "st_file_attributes", 0) & 0x400) and (
            stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
    except OSError:
        return False


def _chain(path):
    return all(_plain(p, True) for p in (path, *path.parents))


def _result(result, code):
    return (getattr(result, "returncode", None) == code
            and isinstance(getattr(result, "stdout", None), bytes)
            and isinstance(getattr(result, "stderr", None), bytes)
            and len(result.stdout) <= 65536 and len(result.stderr) <= 65536
            and (bool(result.stderr) if code == 2 else not result.stdout))


SNAPSHOT = '''
import json, sqlite3, sys
c = sqlite3.connect(sys.argv[1], timeout=1)
c.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1048576)
c.set_progress_handler(lambda: 1, 100000)
result = {t: c.execute("SELECT * FROM " + t + " ORDER BY 1 LIMIT 100").fetchall() for t in ("events", "effects")}
result["tables"] = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name LIMIT 100")]
print(json.dumps(result))
c.close()
'''


def grade(candidate_dir, runner):
    root = Path(candidate_dir).absolute()
    checks = []

    def check(name, value):
        checks.append({"id": name, "passed": bool(value)})

    def scenario(name, operations):
        if not _chain(root):
            check(name + ":boundary", False)
            return
        work = root / (".grader-data-F03-" + uuid4().hex)
        work.mkdir()
        db = work / "state.sqlite"
        expected = {}
        for index, (event, payload, mode) in enumerate(operations):
            if not _chain(work):
                check(name + ":boundary", False)
                return
            argv = ["main.py", str(db.relative_to(root)), event, payload]
            if mode == "crash":
                argv.append("--crash-after-effect")
            elif mode == "badflag":
                argv.append("--unknown")
            result = runner(argv, cwd=root)
            code = 75 if mode == "crash" else 2 if mode in ("reject", "badflag") else 0
            check(f"{name}:{index}:exit", _result(result, code))
            if mode == "ok":
                expected[event] = payload
            # Never follow candidate-created links/reparse points. Bound every
            # DB/sidecar before handing SQLite parsing to the isolated runner.
            safe = _chain(work) and _plain(db)
            if safe:
                try:
                    safe = all(_plain(p) and p.stat().st_size <= 2097152 for p in work.iterdir())
                except OSError:
                    safe = False
            if not safe:
                check(f"{name}:{index}:state", False)
                return
            snapshot = runner(["-I", "-c", SNAPSHOT, str(db.relative_to(root))], cwd=root)
            actual = None
            if (getattr(snapshot, "returncode", None) == 0
                    and isinstance(getattr(snapshot, "stdout", None), bytes)
                    and len(snapshot.stdout) <= 65536):
                try:
                    actual = json.loads(snapshot.stdout)
                except (ValueError, UnicodeError, RecursionError):
                    pass
            rows = [list(item) for item in sorted(expected.items())]
            check(f"{name}:{index}:state", actual == {"events": rows, "effects": rows, "tables": ["effects", "events"]})

    scenario("idempotent_conflict", [("я2", "Корм\n雪", "ok"), ("я2", "Корм\n雪", "ok"),
             ("я2", "Корм\n雪", "ok"), ("я2", "different", "reject"),
             ("other", "Корм\n雪", "ok"), (" spacing ", "  ", "ok")])
    scenario("crash_rollback_retry", [("old", "committed", "ok"), ("new", "pending", "crash"),
             ("new", "pending", "ok"), ("new", "pending", "ok")])
    scenario("invalid_preserves", [("old", "stable", "ok"), ("", "x", "reject"),
             ("bad", "", "reject"), ("badflag", "x", "badflag")])
    if _chain(root):
        check("arity", _result(runner(["main.py"], cwd=root), 2))
    else:
        check("arity", False)
    return {"case_id": "F03", "criteria": checks,
            "verdict": "PASS" if all(c["passed"] for c in checks) else "FAIL"}
