"""Manage the standalone coding evaluation suite. No model API is called."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sys


ROOT = Path(__file__).resolve().parent
CASE_IDS = tuple([f"F{i:02d}" for i in range(1, 9)] + ["R07", "R08"])


def _files(directory):
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"linked suite entry: {path.name}")
        if path.is_file():
            yield path


def seed_digest(directory):
    digest = hashlib.sha256()
    for path in _files(directory):
        digest.update(path.relative_to(directory).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def verify_repository(base=ROOT):
    base = Path(base)
    provenance = json.loads((base / "provenance.json").read_text(encoding="utf-8"))
    expected = set()
    for entry in provenance["files"]:
        name = entry["path"]
        relative = PurePosixPath(name)
        if (relative.is_absolute() or ".." in relative.parts or "\\" in name
                or relative.parts[0] not in {"cases", "criteria", "seeds"}):
            raise ValueError("invalid provenance path")
        if name in expected:
            raise ValueError("duplicate provenance path")
        expected.add(name)
        path = base / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"missing or linked core file: {name}")
        data = path.read_bytes()
        if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError(f"checksum mismatch: {name}")
    actual = {p.relative_to(base).as_posix()
              for directory in ("cases", "criteria", "seeds")
              for p in _files(base / directory)}
    if actual != expected:
        raise ValueError("core file inventory differs from provenance")
    if len(expected) != 46:
        raise ValueError("expected 46 original core files")
    for case_id in CASE_IDS:
        case = json.loads((base / "cases" / f"{case_id}.json").read_text(encoding="utf-8"))
        oracle = base / "criteria" / f"{case_id}.py"
        if (case["case_id"] != case_id
                or hashlib.sha256(oracle.read_bytes()).hexdigest() != case["oracle_sha"]
                or seed_digest(base / "seeds" / case_id) != case["seed_sha"]):
            raise ValueError(f"case manifest mismatch: {case_id}")
    return {"status": "VERIFIED", "cases": len(CASE_IDS), "original_files": len(expected)}


def prepare_case(case_id, destination, base=ROOT):
    if case_id not in CASE_IDS:
        raise ValueError("unknown case")
    verify_repository(base)
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("destination must not already exist")
    source = Path(base) / "seeds" / case_id
    shutil.copytree(source, destination)
    return {"case_id": case_id, "workspace": str(destination),
            "brief": str(destination / "BRIEF.md"),
            "files": [p.relative_to(destination).as_posix() for p in _files(destination)]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list the ten coding tasks")
    commands.add_parser("verify", help="verify original file and case checksums")
    prepare = commands.add_parser("prepare", help="copy starter files into a new workspace")
    prepare.add_argument("case_id", choices=CASE_IDS)
    prepare.add_argument("destination", type=Path)
    grade = commands.add_parser("grade", help="grade a stopped candidate in Linux isolation")
    grade.add_argument("case_id", choices=CASE_IDS)
    grade.add_argument("candidate", type=Path)
    grade.add_argument("--confirmed-stopped", action="store_true")
    grade.add_argument("--strict-export", action="store_true",
                       help="reject extra source entries instead of selecting registered source files")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_case(args.case_id, args.destination)
        else:
            result = verify_repository()
            if args.command == "list":
                result = []
                for case_id in CASE_IDS:
                    case = json.loads((ROOT / "cases" / f"{case_id}.json").read_text(encoding="utf-8"))
                    brief = (ROOT / case["brief"]).read_text(encoding="utf-8").splitlines()[0]
                    result.append({"case_id": case_id, "level": case["level"],
                                   "task": brief.removeprefix("# ")})
            elif args.command == "grade":
                from tools.sandbox import grade_candidate
                result = grade_candidate(args.candidate, args.case_id,
                                         confirmed_stopped=args.confirmed_stopped,
                                         functional_source_only=not args.strict_export)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == "grade":
            if result["status"] != "GRADED":
                return 2
            return 0 if result["result"]["verdict"] == "PASS" else 1
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"status": "ERROR", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
