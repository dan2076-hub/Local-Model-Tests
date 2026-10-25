"""Prepared baseline CLI: add status/tag filters to list."""
import argparse
import json
import sqlite3
import sys
from . import store


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--db", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", allow_abbrev=False)
    add.add_argument("id")
    add.add_argument("title")
    add.add_argument("--tag")
    sub.add_parser("list", allow_abbrev=False)
    done = sub.add_parser("done", allow_abbrev=False)
    done.add_argument("id")
    args = parser.parse_args()
    try:
        if not args.db:
            raise ValueError("empty DB")
        if args.command == "add":
            if args.tag == "":
                raise ValueError("empty supplied tag")
            result = store.add_task(args.db, args.id, args.title, args.tag or "")
        elif args.command == "done":
            result = store.mark_done(args.db, args.id)
        else:
            result = store.list_tasks(args.db)
        print(json.dumps(result, ensure_ascii=True))
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
