"""Prepared one-shot schema-v1 exporter. Add the full staged contract."""
import json
from pathlib import Path
import sys


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def main():
    if len(sys.argv) != 4:
        raise ValueError("usage: main.py REQUEST STATE OUTPUT")
    source, state, output = map(Path, sys.argv[1:])
    request = json.loads(source.read_bytes())
    if request["schema_version"] != 1:
        raise ValueError("legacy exporter supports v1 only")
    result = {"schema_version": 1, "order_id": request["order_id"],
              "items": sorted(request["items"], key=lambda item: item["id"])}
    output.write_bytes(canonical(result))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
