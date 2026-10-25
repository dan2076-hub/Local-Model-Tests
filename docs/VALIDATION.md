# Standalone project validation

Date: October 25, 2026.

| Check | Result |
|---|---|
| Integrity of the 46 original files | SHA-256 hashes match the source and provenance records |
| Manifest references for the ten tasks | Seed and oracle SHA-256 hashes match |
| Windows, Python 3.11.7 | 32 tests: 18 passed, 14 Linux checks skipped |
| WSL Ubuntu, Python 3.12.3, dedicated non-root user | All 32 tests passed |

The Linux checks actually run synthetic programs in bubblewrap to test network and file isolation; storage, time, and output limits; child process termination; functional export without Python caches; an F01 failure scenario; and F05 grading against the provided F06 starter. No completed model solutions are used for these checks.

The initial run under the shared WSL account encountered `RLIMIT_NPROC`: additional threads belonging to the shared UID prevented namespace creation. A check without this limiter confirmed that bubblewrap was available; switching to an existing dedicated user allowed the entire suite to pass. Linux settings and system limits were not changed. The cause and recommendation are documented in the README.

To reproduce from the project root:

```console
python -B local_model_tests.py verify
python -B -m unittest discover -s tests -v
```

These counts refer to the standalone suite's self-tests, rather than an evaluation of any particular model's capabilities. No model calls were made while preparing or validating the package.
