# F07: job lifecycle simulator

Implement `python main.py events.json result.json` using Python 3.11+ standard library only; edit only `main.py`. This is an offline simulator: no real submissions, subprocesses, network or services.

Input is UTF-8 JSON, exactly {"tasks":[ID,...],"events":[EVENT,...]}. IDs are unique nonempty strings; preserve Unicode/whitespace. Initially every task is queued with submit_count=0 and global mode="open". Event objects have exactly these fields:
- {"op":"dispatch","id":ID,"resources":BOOLEAN,"outcome":"accepted"|"timeout"}
- {"op":"poll","id":ID,"result":"running"|"completed"|"timeout"}
- {"op":"reconcile","id":ID,"result":"running"|"completed"|"absent"}
- {"op":"stop","mode":"normal"|"fast"}

All IDs must exist. Validate the ENTIRE input, including events that would be ignored, before publishing output. Reject duplicate object keys, missing/extra keys, nonfinite numbers, incorrect shapes/types, integer substitutes for booleans, and invalid enum values.

Dispatch acts only while mode=open and task state is queued or waiting_resources. resources=false sets waiting_resources without submitting. resources=true increments submit_count once, then sets running for accepted or unknown for timeout (the request may already have been accepted). Never resubmit a task that has submitted once. Other dispatches are ignored.
Poll acts only on running: running leaves it running, completed sets completed, timeout sets unknown. Poll on unknown MUST leave unknown, even if its result says completed. Only explicit reconcile resolves unknown: running→running, completed→completed, absent→failed. Reconcile on any other state is ignored.
Normal stop sets mode=draining and forbids new dispatch; running/unknown tasks may continue via poll/reconcile. Whenever draining has no running or unknown tasks, mode becomes stopped. Queued/waiting tasks are retained. Fast stop immediately sets running/unknown tasks to interrupted and mode=stopped, preserving submit counts and unsubmitted states. Stop after stopped is a no-op; a fast stop during draining interrupts remaining active tasks. Terminal completed/failed/interrupted states never change.

Output is exactly {"mode":MODE,"tasks":[{"id":ID,"state":STATE,"submit_count":INTEGER},...]}, sorted by Python Unicode ID order. Counts must be JSON integers (not booleans). Empty tasks/events are valid. Repeated runs yield identical output bytes; JSON key order/escaping/whitespace are otherwise flexible.

Success exits 0 with empty stdout/stderr. Any invalid input, unreadable/missing input, invalid UTF-8, incorrect arity, or identical input/output path exits 2 with empty stdout and nonempty stderr. Preserve input bytes always; on failure preserve an existing output byte-for-byte or leave it absent. Publish only a complete validated result. No unrelated files.
