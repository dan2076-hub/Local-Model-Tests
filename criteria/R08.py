"""Private oracle: invoke ONLY after agent stop on a separate candidate export.

runner(argv, cwd=Path) is the sole execution boundary: OS isolation, bounded
runtime/output/storage, and termination of ALL candidate writers before return.
No candidate imports or execution in this host grader. Keep oracle and vectors
outside the active model workspace.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
from uuid import uuid4

CASE = "R08"


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


def _names(path):
    if not _chain(path):
        return None
    try:
        with os.scandir(path) as entries:
            names = set()
            for entry in entries:
                names.add(entry.name)
                if len(names) > 8:
                    return None
            return names
    except OSError:
        return None

def grade(candidate_dir, runner):
    root = Path(candidate_dir).absolute()
    checks = []
    stages = ["plan", "export", "verify"]

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

    def fresh(name):
        if not _chain(root):
            check(name + ":boundary", False)
            return None
        work = root / (".grader-data-" + CASE + "-" + uuid4().hex)
        work.mkdir()
        owned_work.add(work)
        return work

    def request(version=2):
        items = [{"id": "я2", "text": "Корм\n雪"}, {"id": "A", "text": "quote \" ,"}]
        if CASE == "R08":
            items = [{"id": "雪-4", "text": "Строка\nдва"}, {"id": "z", "text": "  "}, {"id": "9", "text": ""}]
        return dict(schema_version=version, order_id="order-Я" if CASE == "F08" else "заказ-雪9",
                    steps=stages, items=items)

    def payload(doc):
        base = {k: doc[k] for k in ("schema_version", "order_id", "items")}
        base["items"] = sorted(base["items"], key=lambda r: r["id"])
        if doc["schema_version"] == 2:
            base["manifest"] = {"sha256": hashlib.sha256(_canonical(base)).hexdigest()}
        return _canonical(base)

    def journal(raw, count):
        return _canonical(dict(request_sha256=hashlib.sha256(raw).hexdigest(),
                               completed=stages[:count], counts={stage: int(i < count) for i, stage in enumerate(stages)}))

    seam_work = fresh("fresh_failure_seam")
    if seam_work is not None:
        doc = request(2)
        raw = _canonical(doc) + b"\n"
        source, state, output = (seam_work / n for n in ("request.json", "state.json", "output.json"))
        draft = Path(str(state) + ".draft")
        source.write_bytes(raw)
        output.write_bytes(b"previous output")
        argv = ["main.py", *(str(p.relative_to(root)) for p in (source, state, output))]
        result = runner([*argv, "--fail-before-publish", "--through", "verify"], cwd=root)
        check("fresh_failure_seam:preserve", _exit(result, 2) and _read(source) == raw
              and _read(state) == journal(raw, 2) and _read(draft) == payload(doc)
              and _read(output) == b"previous output")
        if _chain(seam_work):
            result = runner(argv, cwd=root)
            check("fresh_failure_seam:resume", _exit(result, 0) and _read(source) == raw
                  and _read(state) == journal(raw, 3) and _read(draft) == payload(doc)
                  and _read(output) == payload(doc))

    empty_work = fresh("empty_items")
    if empty_work is not None:
        doc = {**request(1), "items": []}
        raw = _canonical(doc)
        source, state, output = (empty_work / n for n in ("request.json", "state.json", "output.json"))
        source.write_bytes(raw)
        result = runner(["main.py", *(str(p.relative_to(root)) for p in (source, state, output))], cwd=root)
        check("empty_items", _exit(result, 0) and _read(source) == raw and _read(output) == payload(doc)
              and _read(state) == journal(raw, 3) and _read(Path(str(state) + ".draft")) == payload(doc)
              and _names(empty_work) == {"request.json", "state.json", "output.json", "state.json.draft"})

    for version in (1, 2):
        name = "resume_v" + str(version)
        work = fresh(name)
        if work is None:
            continue
        source, state, output = (work / n for n in ("request.json", "state.json", "output.json"))
        draft = Path(str(state) + ".draft")
        doc = request(version)
        raw = _canonical(doc) + b"\n"
        wanted = payload(doc)
        source.write_bytes(raw)
        alternate = work / "same-content-new-bytes.json"
        alternate.write_bytes(_canonical(doc))
        output.write_bytes(b"previous output")
        if CASE == "F08":
            sequence = [(["--through", "plan"], 1, 0), (["--through", "export"], 2, 0), (["--through", "plan"], 2, 0)]
        else:
            sequence = [(["--through", "export"], 2, 0), (["--through", "plan"], 2, 0)]
        sequence += [(["--fail-before-publish"], 2, 2), ([], 3, 0),
                     (["--through", "plan"], 3, 0), ([], 3, 0)]
        published_signature = None
        for index, (flags, count, code) in enumerate(sequence):
            label = f"{name}:{index}"
            if not _chain(work):
                check(label + ":boundary", False)
                break
            result = runner(["main.py", *(str(p.relative_to(root)) for p in (source, state, output)), *flags], cwd=root)
            check(label + ":exit", _exit(result, code))
            check(label + ":input", _read(source) == raw and _read(alternate) == _canonical(doc))
            check(label + ":journal", _read(state) == journal(raw, count))
            check(label + ":draft", _read(draft) == wanted if count >= 2 else _absent(draft))
            check(label + ":output", _read(output) == (wanted if count == 3 else b"previous output"))
            check(label + ":cleanup", _names(work) == {"request.json", "state.json", "output.json", "same-content-new-bytes.json"}
                  | ({"state.json.draft"} if count >= 2 else set()))
            if count == 3:
                signature = None
                if _read(output) == wanted:
                    try:
                        signature = output.stat().st_mtime_ns
                    except OSError:
                        pass
                if published_signature is not None:
                    check(label + ":no_republish", signature == published_signature)
                published_signature = signature
        if _chain(work):
            result = runner(["main.py", *(str(p.relative_to(root)) for p in (alternate, state, output))], cwd=root)
            check(name + ":request_binding", _exit(result, 2) and _read(state) == journal(raw, 3)
                  and _read(output) == wanted and _read(draft) == wanted and _read(source) == raw
                  and _read(alternate) == _canonical(doc))

    def reject(name, raw, state_data=None, draft_data=None, alias=None, flags=None, directory=False):
        work = fresh(name)
        if work is None:
            return
        source, state, output = (work / n for n in ("request.json", "state.json", "output.json"))
        draft = Path(str(state) + ".draft")
        initial = {source: raw, state: state_data, draft: draft_data, output: b"previous output"}
        for path, data in initial.items():
            if path == output and directory:
                path.mkdir()
            elif data is not None:
                path.write_bytes(data)
        if alias == "input_output":
            target = source
        elif alias == "state_output":
            target = state
        elif alias == "draft_output":
            target = draft
        else:
            target = output
        result = runner(["main.py", *(str(p.relative_to(root)) for p in (source, state, target)), *(flags or [])], cwd=root)
        check(name + ":exit", _exit(result, 2))
        check(name + ":preserve", all(_names(p) == set() if p == output and directory else
              _read(p) == data if data is not None else _absent(p) for p, data in initial.items()))
        check(name + ":cleanup", _names(work) == {p.name for p, data in initial.items() if data is not None})

    doc = request()
    raw = _canonical(doc)
    bad = {
        "bool_version": {**doc, "schema_version": True}, "unsupported_version": {**doc, "schema_version": 3},
        "empty_order": {**doc, "order_id": ""}, "step_order": {**doc, "steps": list(reversed(stages))},
        "duplicate_items": {**doc, "items": [doc["items"][0], doc["items"][0]]},
        "bad_item": {**doc, "items": [{"id": "x", "text": 1}]},
        "extra_field": {**doc, "extra": None},
    }
    for name, value in bad.items():
        reject(name, _canonical(value))
    reject("invalid_utf8", b"\xff")
    reject("malformed_json", b"{")
    reject("duplicate_key", b'{"schema_version":1,"schema_version":2}')
    reject("missing_input", None)
    reject("directory_output", raw, directory=True)
    reject("malformed_state", raw, state_data=b"{")
    reject("wrong_binding", raw, state_data=journal(raw + b"\n", 1))
    invalid_state = _json(journal(raw, 2))
    invalid_state["counts"]["plan"] = 2
    reject("inconsistent_counts", raw, state_data=_canonical(invalid_state))
    reject("orphan_draft", raw, draft_data=b"unrelated")
    reject("corrupt_draft", raw, state_data=journal(raw, 2), draft_data=b"bad draft")
    reject("missing_draft", raw, state_data=journal(raw, 2))
    reject("verified_output_conflict", raw, state_data=journal(raw, 3), draft_data=payload(doc))
    for alias in ("input_output", "state_output", "draft_output"):
        reject(alias, raw, alias=alias)
    for index, flags in enumerate([
        ["--through", "bad"], ["--through"], ["--unknown"],
        ["--through", "plan", "--through", "export"],
        ["--fail-before-publish", "--fail-before-publish"],
        ["--through", "export", "--fail-before-publish"],
    ]):
        reject("invalid_flags_" + str(index), raw, flags=flags)
    check("arity", _chain(root) and _exit(runner(["main.py"], cwd=root), 2))
    return {"case_id": CASE, "criteria": checks,
            "verdict": "PASS" if all(c["passed"] for c in checks) else "FAIL"}
