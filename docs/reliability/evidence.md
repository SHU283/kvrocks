# Recorded validation and its limits

The checked-in records describe a completed run of `tools/reliability/run.py` from the publication checkout on **2026-11-17, 13:25:44-13:29:53 UTC**. Read [validation.json](../../examples/reliability/validation.json) for exact timestamps, commands, counts, statuses, and build identity. These are recorded observations, not guarantees for another machine or a performance claim.

## Results

| Check | Recorded result | Meaning |
|---|---|---|
| Full C++ suite | 681 tests in 87 suites: 680 passed, 1 skipped, 0 failed | Includes unchanged upstream tests and the four added socket cases |
| Original socket control | Named deadline test failed with the designated watchdog diagnostic | The intended regression was reproduced; the original implementation did not pass |
| Patched socket control | 5 passed, 0 failed | Four added cases plus the existing listening-address test |
| Replication and process recovery | 5 of 5 stages passed | Values, update, deletion, offset, process restart, and continued replication checked |
| Paused-replica backpressure | 4 of 4 stages passed | Real timeout evidence, connection cleanup, resume, data catch-up, and continued replication checked |

The skipped case is upstream `UseBitmap/RedisBitmapTest.BitfieldStringGetSetTest/0`, which calls `GTEST_SKIP`. It was not converted into a pass, removed, or modified for this project. The full count is not the number of personally authored tests.

## Environment and build provenance

The host was Ubuntu 24.04.3 under WSL2, kernel 6.6.87.2, with GCC 13.3.0 and Python 3.12.3. The full server and unit-test executables were **reused from the existing development build**. The socket control compiled its original and patched translation units separately for this verification. No fresh full server build was performed during this run.

The publication runner matched 373 source/build-input files and checked compile-command membership, input timestamps, and binary hashes before and after execution. See [provenance.json](../../examples/reliability/provenance.json) and the build identity in [validation.json](../../examples/reliability/validation.json). These checks document consistency; they do not prove a hermetic build or independently rebuild dependencies.

The reused server's version string contains the development snapshot marker `7ecd365`. That is an embedded marker from the original local build, **not** the official Apache baseline or a commit in this publication branch. The official source baseline for the contribution is `baa1da67f2ef94ffd62823116537ab7e95e7d5ba`. Keeping the marker in the provenance record makes the reuse visible rather than presenting the executable as newly built from the publication commit.

## Evidence files

| Record | Contents |
|---|---|
| [validation.json](../../examples/reliability/validation.json) | Overall result, execution details, full C++ counts, and build consistency checks |
| [socket-control.json](../../examples/reliability/socket-control.json) | Original and patched control outcomes and source identity |
| [socket-before.txt](../../examples/reliability/socket-before.txt), [socket-after.txt](../../examples/reliability/socket-after.txt) | Expected original failure and patched GoogleTest output |
| [cpp-tests.excerpt.txt](../../examples/reliability/cpp-tests.excerpt.txt) | Focused test output, skipped test, and full-suite summary |
| [replication.json](../../examples/reliability/replication.json) | Five-stage data and process-recovery observations |
| [backpressure.json](../../examples/reliability/backpressure.json) | Four-stage fault experiment, workload, timeout evidence, and recovery |
| [availability.jsonl](../../examples/reliability/availability.jsonl) | Individual master probe observations during the backpressure run |
| [provenance.json](../../examples/reliability/provenance.json) | Environment, build origin, version marker, sanitization, and scope |

Machine-specific paths use placeholders such as `${CHECKOUT}`, `${RUN}`, `${BUILD}`, `${COMPILED_SOURCE}`, and `${SOCKET_BUILD}`. Counts, timestamps, hashes, exit codes, faults, and measured values are retained. Generated databases, dependency archives, binaries, caches, and unbounded logs are not part of the published evidence.

## How to interpret the backpressure result

The default write workload is 128 values of 256 KiB, totaling 32 MiB of generated value payload, plus small seed, update, deletion, and follow-up operations. The master uses a 1000 ms replication send timeout. This is a bounded fault-triggering workload, not a throughput benchmark or a claim about network bytes transferred.

The observed timeout reports `send timeout waiting for socket, sent 0 of 262219 bytes`. It proves the real-node experiment reached its timeout path and then recovered. Because no bytes were sent in that attempt, it does not by itself show the old implementation blocking inside `write`. The before/after socket control supplies the direct evidence for that defect.

After `SIGCONT`, the experiment verified 131 expected keys, including the deleted key, at offset 133 in 3.5452 seconds. A subsequent write reached offset 134. All 68 sampled master `PING`/`GET` probes succeeded. These values come from [backpressure.json](../../examples/reliability/backpressure.json) and apply only to this recorded run.

Passing master probes and sampled resource values describe this run only. They do not establish production availability, capacity, latency percentiles under load, or an improvement over the original implementation. Recovery time depends on scheduling and this workload; it is not a service-level objective.

## Outside the recorded scope

The run does not validate a fresh full build, the Go integration suite, TLS, other operating systems, host power loss, storage-device failure, automatic failover, consensus, linearizability, or a throughput improvement. Synchronous WAL writes plus `SIGKILL` model process crashes, not host or storage failure. Replication remains asynchronous, and the lab does not classify every recovery as full or incremental synchronization.

For new results, run the [reproduction commands](reproduction.md) and retain the resulting success, failure, and skip outcomes. Do not treat the checked-in record as a substitute for execution on a new environment.
