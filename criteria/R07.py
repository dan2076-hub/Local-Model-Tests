"""Private oracle: invoke ONLY after agent stop on a separate candidate export.

runner(argv, cwd=Path) is the sole execution boundary: OS isolation, bounded
runtime/output/storage, and termination of ALL candidate writers before return.
No candidate imports or execution in this host grader. Keep oracle and vectors
outside the active model workspace.
"""
import hashlib
import json
from pathlib import Path
import stat
from uuid import uuid4

CASE = "R07"


def _plain(path, directory=False):
    try:
        info = path.lstat()
        return not (getattr(info, "st_file_attributes", 0) & 0x400) and (
            stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
    except OSError:
        return False


def _chain(path):
    return all(_plain(p, True) for p in (path, *path.parents))


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


def _read(path, limit=65536):
    try:
        if _chain(path.parent) and _plain(path) and path.stat().st_size <= limit:
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


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def _json(data):
    if not isinstance(data, bytes) or len(data) > 65536:
        return None
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")))
    except (ValueError, UnicodeError, RecursionError):
        return None


def _exit(result, code):
    return (getattr(result, "returncode", None) == code
            and isinstance(getattr(result, "stdout", None), bytes)
            and isinstance(getattr(result, "stderr", None), bytes)
            and not result.stdout and len(result.stderr) <= 65536
            and (bool(result.stderr) if code == 2 else not result.stderr))


def _canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")

def grade(candidate_dir, runner):
    root = Path(candidate_dir).absolute()
    checks = []

    def check(name, passed):
        checks.append({"id": name, "passed": bool(passed)})

    execute = runner
    owned_work = set()
    initial_inventory = _inventory(root, owned_work)
    invocations = 0

    def runner(argv, *, cwd):
        nonlocal invocations
        result = execute(argv, cwd=cwd)
        valid = initial_inventory is not None and _inventory(root, owned_work) == initial_inventory
        allowed = ({"events.json", "result.json"} if CASE.endswith("07") else
                   {"request.json", "state.json", "state.json.draft", "output.json", "same-content-new-bytes.json"})
        for work in owned_work:
            inventory = _inventory(work, set())
            valid = valid and inventory is not None and set(inventory) <= allowed
        check("inventory:" + str(invocations), valid)
        invocations += 1
        return result

    def run(name, data, expected=None, absent=False, same=False):
        if not _chain(root):
            check(name + ":boundary", False)
            return None
        work = root / (".grader-data-" + CASE + "-" + uuid4().hex)
        work.mkdir()
        owned_work.add(work)
        source, output = work / "events.json", work / "result.json"
        if data is not None:
            source.write_bytes(data)
        if not absent:
            output.write_bytes(b"previous output")
        target = source if same else output
        result = runner(["main.py", str(source.relative_to(root)), str(target.relative_to(root))], cwd=root)
        check(name + ":input", _read(source) == data if data is not None else _absent(source))
        content = _read(target)
        if expected is not None:
            actual = _json(content)
            typed = (isinstance(actual, dict) and set(actual) == {"mode", "tasks"}
                     and isinstance(actual["tasks"], list) and all(
                         isinstance(row, dict) and set(row) == {"id", "state", "submit_count"}
                         and type(row["submit_count"]) is int for row in actual["tasks"]))
            check(name + ":result", _exit(result, 0) and typed and actual == expected)
        else:
            preserved = content == data if same else _absent(output) if absent else content == b"previous output"
            check(name + ":reject_preserve", _exit(result, 2) and preserved)
        return content

    ids = ["я2", "A", "b"] if CASE == "F07" else ["雪9", "Z", "r-4"]
    wait, unknown, active = ids
    def dispatch(key, resources=True, outcome="accepted"):
        return dict(op="dispatch", id=key, resources=resources, outcome=outcome)
    def poll(key, result):
        return dict(op="poll", id=key, result=result)
    def reconcile(key, result):
        return dict(op="reconcile", id=key, result=result)
    def document(events, tasks=None):
        return _canonical(dict(tasks=ids if tasks is None else tasks, events=events))
    def result(mode, states):
        return dict(mode=mode, tasks=[dict(id=key, state=state, submit_count=count)
                    for key, state, count in sorted(states)])
    events = [dispatch(wait, False), dispatch(active), dispatch(unknown, outcome="timeout"),
              dispatch(unknown), poll(unknown, "completed"), dict(op="stop", mode="normal"),
              dispatch(wait), poll(active, "completed"), reconcile(unknown, "completed")]
    if CASE == "R07":
        events = [poll(wait, "running"), dispatch(wait, False), dispatch(unknown, outcome="timeout"),
                  dispatch(active), poll(unknown, "timeout"), dispatch(unknown), dict(op="stop", mode="normal"),
                  dispatch(wait), reconcile(unknown, "completed"), poll(active, "completed")]
    expected = result("stopped", [(wait, "waiting_resources", 0), (active, "completed", 1), (unknown, "completed", 1)])
    first = run("normal_drain", document(events), expected)
    second = run("repeat", document(events), expected)
    check("deterministic", first is not None and first == second)
    run("resources_recover", document([dispatch(wait, False), dispatch(wait), dispatch(wait)]),
        result("open", [(wait, "running", 1), (active, "queued", 0), (unknown, "queued", 0)]))
    events = [dispatch(unknown, outcome="timeout"), poll(unknown, "running"), poll(unknown, "completed"), dispatch(unknown)]
    run("unknown_requires_reconcile", document(events), result("open", [(wait, "queued", 0), (active, "queued", 0), (unknown, "unknown", 1)]))
    run("reconcile_absent_no_resubmit", document(events + [reconcile(unknown, "absent"), dispatch(unknown)]),
        result("open", [(wait, "queued", 0), (active, "queued", 0), (unknown, "failed", 1)]))
    run("reconcile_running_poll", document(events + [reconcile(unknown, "running"), poll(unknown, "timeout"), reconcile(unknown, "completed")]),
        result("open", [(wait, "queued", 0), (active, "queued", 0), (unknown, "completed", 1)]))
    fast = [dispatch(wait, False), dispatch(active), dispatch(unknown, outcome="timeout"),
            dict(op="stop", mode="normal"), dict(op="stop", mode="fast"),
            poll(active, "completed"), reconcile(unknown, "completed"), dispatch(wait), dict(op="stop", mode="normal")]
    run("fast_interrupt", document(fast), result("stopped", [(wait, "waiting_resources", 0), (active, "interrupted", 1), (unknown, "interrupted", 1)]))
    run("fast_preserves_completed", document([dispatch(active), poll(active, "completed"), dispatch(unknown, outcome="timeout"),
        dict(op="stop", mode="fast"), poll(unknown, "completed")]),
        result("stopped", [(wait, "queued", 0), (active, "completed", 1), (unknown, "interrupted", 1)]))
    run("stop_before_dispatch", document([dict(op="stop", mode="normal"), dispatch(wait)]),
        result("stopped", [(key, "queued", 0) for key in ids]))
    run("empty", document([], []), dict(mode="open", tasks=[]))
    bad = {
        "bool": document([dict(op="dispatch", id=wait, resources=1, outcome="accepted")]),
        "unknown_id": document([dispatch("not-a-task")]),
        "invalid_ignored": document([dict(op="stop", mode="fast"), dict(op="poll", id=active, result="bad")]),
        "extra": document([dict(op="stop", mode="normal", extra=1)]),
        "unknown_op": document([dict(op="delete", id=wait)]),
        "duplicate_ids": document([], [wait, wait]),
        "empty_id": document([], [""]),
        "schema": b'{"tasks":[],"events":[],"extra":0}',
        "duplicate_key": b'{"tasks":[],"tasks":[],"events":[]}',
        "syntax": b'{', "utf8": b'\xff',
    }
    for name, data in bad.items():
        run(name, data)
    run("absent_output", bad["bool"], absent=True)
    run("same_path", document([]), same=True)
    run("missing_input", None)
    check("arity", _chain(root) and _exit(runner(["main.py"], cwd=root), 2))
    return {"case_id": CASE, "criteria": checks,
            "verdict": "PASS" if all(c["passed"] for c in checks) else "FAIL"}
