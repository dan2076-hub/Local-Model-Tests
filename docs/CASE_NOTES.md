# Case notes

## F05: an empty SQLite path

The original specification explicitly prohibits empty ID and title values, and an empty tag when supplied. The `--db` argument is required, but there is no separate requirement that its path value be nonempty. SQLite interprets an empty path as a temporary database, whereas the historical grading suite expects an error for `--db ""`.

The original specification and criterion are preserved unchanged. A failure on this criterion alone should therefore be reported separately as a contract ambiguity. Retain the raw score for historical comparisons; if you clarify the contract for a new study, publish a separate version of the specification and criteria in advance and evaluate all participants under the same conditions.

## F08 and R08: the output path is a directory

The `directory_output` checks actually create a directory. The tasks require artifacts to be preserved when this error occurs, so creating intermediate state/draft artifacts may violate the contract even if the output directory itself is preserved. These checks are retained in their original form; they test functional path handling, rather than imposing a restriction on Python caches.

## F06: the provided baseline implementation

The working queue in the F06 starter is part of the input for an extension task, rather than a study participant's solution. Since its functionality is close to F05, provide tasks in separate working directories without access to the rest of the repository. R07/R08 are also independent attempts, rather than continuations of already corrected F07/F08 solutions.

## Separating grading from the environment

The oracle exposes `grade(candidate_dir, runner)` and checks only the specified contract. The runner handles execution, isolation, and resource limits. Missing bubblewrap, prohibited user namespaces, or a runner startup failure are reported as infrastructure errors. Stop all agent processes before using `--confirmed-stopped`: this flag records that the condition has been met, but does not itself stop an external agent.

The original historical budgets in JSON are not settings for a new model. Before making a comparison, separately record its version, quantization, context, reasoning, available tools, assistance rules, and the experiment's actual limits.
