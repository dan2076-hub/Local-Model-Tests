"""Private oracle for stopped exports. Execution ONLY through injected runner.

Runner must bound time/output and terminate ALL candidate writers before
returning, including descendants launched by the fixed concurrency driver.
No candidate code executes in this grader process. The real filesystem race
runs inside that same OS isolation, never while the model agent is active.
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


def _read(path):
    try:
        if _chain(path.parent) and _plain(path) and path.stat().st_size <= 1048576:
            return path.read_bytes()
    except OSError:
        pass
    return None


def _absent(path):
    if not _chain(path.parent):
        return False
    try:
        path.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        pass
    return False


def _names(path):
    if _chain(path):
        try:
            return {p.name for p in path.iterdir()}
        except OSError:
            pass
    return None


def _result(result, code):
    return (getattr(result, "returncode", None) == code
            and isinstance(getattr(result, "stdout", None), bytes)
            and isinstance(getattr(result, "stderr", None), bytes)
            and len(result.stdout) <= 65536 and len(result.stderr) <= 65536
            and (bool(result.stderr) if code == 2 else not result.stdout))


# Operator-authored driver, executed inside the runner, never on the host.
# communicate bounds runtime; redirected temporary streams avoid unbounded
# pipe buffering. No looping retries and no candidate imports.
RACE = '''
import concurrent.futures, json, subprocess, sys, tempfile, threading, time
barrier = threading.Barrier(len(sys.argv) - 2)
done = threading.Event()
partial = []
expected = []
for source in sys.argv[2:]:
    with open(source, "rb") as f: expected.append(f.read(1048577))
def observe():
    for _ in range(15000):
        if done.is_set(): return
        try:
            with open(sys.argv[1], "rb") as f: data = f.read(1048577)
            if data not in expected: partial.append(True); return
        except FileNotFoundError:
            pass
        except OSError:
            partial.append(True); return
        done.wait(0.001)
watcher = threading.Thread(target=observe)
watcher.start()
def run(source):
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        barrier.wait(timeout=5)
        p = subprocess.Popen([sys.executable, "-B", "main.py", source, sys.argv[1]], stdout=out, stderr=err)
        try:
            code = p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill(); p.wait(); code = -999
        out.seek(0); err.seek(0)
        return [code, len(out.read(65537)), len(err.read(65537))]
with concurrent.futures.ThreadPoolExecutor(max_workers=len(sys.argv)-2) as pool:
    records = list(pool.map(run, sys.argv[2:]))
done.set(); watcher.join(timeout=2)
print(json.dumps({"records": records, "partial": bool(partial)}))
'''


def grade(candidate_dir, runner):
    root = Path(candidate_dir).absolute()
    checks = []

    def check(name, value):
        checks.append({"id": name, "passed": bool(value)})

    def workdir(name):
        if not _chain(root):
            check(name + ":boundary", False)
            return None
        path = root / (".grader-data-F04-" + uuid4().hex)
        path.mkdir()
        return path

    def single(name, data, previous=None, code=0, missing=False, same=False, directory=False, source_directory=False):
        work = workdir(name)
        if work is None:
            return
        src, dst = work / "source.bin", work / "published.bin"
        if source_directory:
            src.mkdir()
        elif not missing:
            src.write_bytes(data)
        if previous is not None:
            dst.write_bytes(previous)
        if directory:
            dst.mkdir()
        target = src if same else dst
        result = runner(["main.py", str(src.relative_to(root)), str(target.relative_to(root))], cwd=root)
        check(name + ":exit", _result(result, code))
        check(name + ":source", _names(src) == set() if source_directory else
              _absent(src) if missing else _read(src) == data)
        expected = data if same or code == 0 else previous
        if directory:
            preserved = _names(dst) == set()
        elif expected is None:
            preserved = _absent(dst)
        else:
            preserved = _read(target) == expected
        check(name + ":destination", preserved)
        names = _names(work)
        check(name + ":cleanup", names is not None and names <= {"source.bin", "published.bin"})

    single("binary", bytes(range(256)) * 1024)
    single("empty", b"")
    single("same_bytes", b"\x00same\xff", b"\x00same\xff")
    single("conflict", b"new", b"committed", 2)
    single("missing", b"", code=2, missing=True)
    single("missing_preserve", b"", b"committed", 2, missing=True)
    single("same_path", b"keep", code=2, same=True)
    single("directory", b"keep", code=2, directory=True)
    single("source_directory", b"", code=2, source_directory=True)
    for trial in range(3):
        name = f"race_{trial}"
        work = workdir(name)
        if work is None:
            continue
        sources = [work / (str(i) + ".bin") for i in range(6)]
        contents = [(bytes([i % 3]) + b"\x00winner\xff") * 32768 for i in range(6)]
        for path, content in zip(sources, contents):
            path.write_bytes(content)
        dst = work / "winner.bin"
        result = runner(["-I", "-c", RACE, str(dst.relative_to(root)),
                         *(str(p.relative_to(root)) for p in sources)], cwd=root)
        records = None
        if (getattr(result, "returncode", None) == 0
                and isinstance(getattr(result, "stdout", None), bytes) and len(result.stdout) <= 65536):
            try:
                report = json.loads(result.stdout)
                if isinstance(report, dict) and report.get("partial") is False:
                    records = report.get("records")
            except (ValueError, UnicodeError, RecursionError):
                pass
        winner = _read(dst)
        valid = isinstance(records, list) and len(records) == len(sources) and winner in contents
        if valid:
            for record, content in zip(records, contents):
                code = 0 if content == winner else 2
                valid = valid and (isinstance(record, list) and len(record) == 3
                    and all(type(v) is int for v in record) and record[0] == code
                    and record[1] == 0 and 0 <= record[2] <= 65536
                    and (record[2] > 0 if code == 2 else True))
        check(name + ":winner_and_status", valid)
        check(name + ":sources", all(_read(p) == data for p, data in zip(sources, contents)))
        check(name + ":cleanup", _names(work) == {p.name for p in sources} | {"winner.bin"})
    check("arity", _chain(root) and _result(runner(["main.py"], cwd=root), 2))
    return {"case_id": "F04", "criteria": checks,
            "verdict": "PASS" if all(c["passed"] for c in checks) else "FAIL"}
