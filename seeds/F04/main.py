"""Repair the no-overwrite publication described in BRIEF.md."""
from pathlib import Path
import sys
import time


def publish_file(src, dst):
    src, dst = Path(src), Path(dst)
    data = src.read_bytes()
    if dst.exists():
        if dst.read_bytes() != data:
            raise ValueError("conflict")
        return
    # Broken: another publisher can create dst between this check and write.
    time.sleep(0.05)
    dst.write_bytes(data)


if __name__ == "__main__":
    try:
        if len(sys.argv) != 3:
            raise ValueError("usage: main.py SRC DST")
        publish_file(*sys.argv[1:])
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
