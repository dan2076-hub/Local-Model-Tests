# Local Model Tests

A standalone suite of **10 coding tasks**, with specifications, starter files, and independent automated grading criteria. Use it to compare local models and other participants on the same tasks. The checks are independent of the model name and how it is run.

The suite was extracted from a series of actual test runs. **All 46 original files are preserved byte for byte**; their SHA-256 hashes are recorded in `provenance.json` and the task manifests. Completed model solutions and conversations are not included. F06 includes a working baseline queue that the task asks participants to extend.

## Requirements

- Python **3.11+**, with no third-party Python packages.
- Browsing the suite, verifying integrity, and preparing tasks work on Windows, Linux, and macOS.
- Isolated grading requires **Linux**, a non-root user, and `/usr/bin/python3`, `/usr/bin/bwrap`, and `/usr/bin/prlimit`. On Ubuntu, install the dependencies with `sudo apt install python3 bubblewrap util-linux`.
- On Windows, run the grader through an existing WSL Ubuntu installation. The kernel and system policy must allow nested unprivileged user namespaces; if they are unavailable, grading returns an infrastructure error.

A dedicated unprivileged Linux user is recommended for grading. The process limit counts all threads belonging to the same system UID; in WSL, a shared account may have additional processes in other namespaces. If bubblewrap reports `Creating new namespace failed: Resource temporarily unavailable` but runs without the limiter, check the UID's resource usage and run the tests under a dedicated user. The package does not change system quotas automatically.

## Quick start

From the project root:

```console
python -B local_model_tests.py verify
python -B local_model_tests.py list
python -B local_model_tests.py prepare F01 candidates/F01
```

Open `candidates/F01/BRIEF.md` and give the model access **only to this prepared directory**. The model implements the specification and runs its own checks. The `criteria/` files and other participants' solutions must be kept outside its workspace during the attempt. Because the checks are public, the independence of the experiment depends on this separation.

Once the agent has finished and been stopped, run the following **on Linux**:

```console
python3 -B local_model_tests.py grade F01 candidates/F01 --confirmed-stopped
```

In WSL, first change to the cloned project directory, for example with `cd /mnt/d/Git/Local-Model-Tests`, then run the command above. To prepare another task, replace `F01` with its ID.

The `prepare` command creates a new directory and preserves existing work if the destination already exists. The grader exports the registered source files into a separate temporary environment. Incidental files such as `__pycache__` do not affect the functional result: the default policy is `functional-source-v1`. Use `--strict-export` for the previous strict check of the directory's complete contents.

## Tasks

| ID | What is tested |
|---|---|
| F01 | CSV → JSON, UTF-8/BOM, sorting, validation, and file preservation |
| F02 | JSON events → CSV, aggregation, strict types, and deterministic output |
| F03 | SQLite events, payload conflicts, and transactional rollback |
| F04 | Publishing a file without overwriting the winner, and idempotency |
| F05 | A persistent CLI queue across multiple files, with participant-written unittest tests |
| F06 | Extending an existing queue with compatible status/tag filters |
| F07 | Job lifecycle, resources, unknown/reconcile, drain/fast |
| F08 | Resumable export, state preservation, and manifest SHA-256 |
| R07 | A separate isomorphic variant of the lifecycle task |
| R08 | A separate isomorphic variant of the resumable export task |

The exact behavior is defined in `seeds/<ID>/BRIEF.md`. The allowed files, checksums, and grading contract are specified in `cases/<ID>.json`. See [CASE_NOTES.md](docs/CASE_NOTES.md) for the F05 ambiguity and additional clarifications.

## Results and limitations

`grade` prints JSON containing individual criteria, a verdict, and isolation details. Exit codes: **0 — PASS, 1 — functional FAIL, 2 — infrastructure or input error**. An environment error is not counted as a model failure.

Grading runs code in two nested bubblewrap environments with isolated networks, processes, and filesystems. The inner environment cannot see the grading suite's source code. Limits apply to execution time, output, process memory, and temporary storage. This is a port of the tested harness; memory limits apply per process rather than as an aggregate cgroup quota.

The project itself does not call a model API, load weights, or configure the model's reasoning or context window. The `max_output_tokens_total`, `max_round_trips`, and `time_limit_seconds` fields in the historical manifests refer to an earlier generation setup and are retained for reproducibility. This project's commands **do not enforce them**; they do not limit a new model attempt. The short sandbox execution limits for the completed program apply to running the checks, not to the model's reasoning time.

Use the same tasks, assistance rules, and attempt settings when making comparisons. The number of individual assertions differs between tasks, so the overall percentage of passed assertions is not, by itself, a universal model score.

## Project validation

```console
python -B local_model_tests.py verify
python -B -m unittest discover -s tests -v
```

On Windows, Linux isolation checks are marked as skipped. On Linux, they actually run and require the installed tools and available user namespaces. The self-tests do not call a model or contain completed solutions to the main tasks.

Validation of the standalone project: [VALIDATION.md](docs/VALIDATION.md).

## Structure

```text
cases/                 Manifests for the ten tasks
seeds/                 Only specifications and intended starter files
criteria/              Independent grading functions: grade(candidate, runner)
tools/sandbox.py       Isolated Linux execution and source export
local_model_tests.py   list / verify / prepare / grade
tests/                 Integrity, suite management, and harness checks
provenance.json        SHA-256 hashes of all 46 original files
docs/CASE_NOTES.md     Notes on interpreting the grading criteria
```
