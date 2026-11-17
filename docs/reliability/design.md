# Validation design and reproduction

The validation combines focused C++ regressions with experiments against two real Kvrocks processes. It is intended to make a small production patch reviewable in the context of an existing service.

## Environment and build

Use Linux or WSL2, Python 3.11 or newer, a C++20 compiler, CMake, Ninja, and the upstream build prerequisites. The development baseline used Ubuntu 24.04, GCC 13.3, and CMake 3.28. The Python tools require no third-party Python packages, Redis client package, or `redis-cli`.

From the repository root:

```bash
python3 x.py build --ninja --unittest -j4
python3 tools/reliability/run.py --build-dir build
```

The runner uses the specified existing build directory and retains its commands, exit codes, binary identity, and structured experiment results. It does not perform a fresh full build. It checks the compiled source tree against this checkout, checks compile-command membership and modification times, and records binary hashes. These are consistency checks, not proof of a fresh or hermetic build. Local output defaults to `artifacts/reliability/`, which is excluded from version control. See [reproduction details](reproduction.md) for build requirements and options.

For a focused C++ run:

```bash
python3 x.py test cpp build --gtest_filter='IOUtil.*:IOUtilSocketTest.*'
```

For the full C++ suite:

```bash
python3 x.py test cpp build
```

The four added cases are `SendWithTimeoutHonorsDeadlineOnBlockingSocket`, `SendWithTimeoutSendsDataAndPreservesSocketFlags`, `SendWithTimeoutKeepsDeadlineAfterInterruptedPoll`, and `SendWithTimeoutCompletesPartialWritesToSlowReader`. Most tests in the complete suite are unchanged upstream tests.

## Replication and process-recovery experiment

```bash
python3 tools/reliability/run_replication_lab.py \
  --binary build/kvrocks --work-dir artifacts/reliability/replication \
  --keys 100 --timeout 60
```

| Stage | Fault or operation | Required observation |
|---|---|---|
| Initial writes | Start master; write and read 100 keys | Expected values on the master |
| Initial replication | Start replica | Link up, applied offset at least the captured target, expected values |
| Replica crash and catch-up | `SIGKILL` replica; add another batch, update one key, delete one key; restart replica | New values, update, deletion, and target offset verified |
| Master crash and persistence | `SIGKILL` master; restart from its existing data directory | Expected values and deletion survive this process crash |
| Replication after restart | Write the third batch | Replica catches up and all 300 keys, including the deleted key, are checked |

Each node uses its own directory and dynamically allocated loopback port. Startup checks verify that `INFO server` reports the expected child PID. The listener remains bound until the server inherits it, avoiding a probe-then-bind port race. The experiment only signals processes that it created.

## Slow-replica backpressure experiment

```bash
python3 tools/reliability/run_backpressure_lab.py \
  --binary build/kvrocks --work-dir artifacts/reliability/backpressure \
  --payload-mib 32 --send-timeout-ms 1000 --timeout 60 --total-timeout 180
```

| Stage | Operation | Required observation |
|---|---|---|
| Initial synchronization | Start nodes and seed three keys | Initial data and offset agree |
| Paused replica | `SIGSTOP` replica; write 128 values of 256 KiB each, update a key, delete a key | Replica is stopped; master logs a real batch-send timeout; timed-out connection is eventually removed |
| Resume and recover | `SIGCONT` replica | All 131 expected keys, including deletion, and the captured offset are verified |
| Continue replication | Write one additional key after recovery | New data and offset reach the replica; independent master probes have no failures in this run |

Payloads are generated deterministically from a fixed seed. The default 32 MiB workload is bounded and the command accepts 16-64 MiB. It is a fault-triggering workload, not a throughput benchmark.

The master sets `replication-send-timeout-ms` before the replica connection is created because the sending thread captures that configuration. `max-replication-lag=0` disables the separate lag threshold so that the experiment can observe send-timeout behavior without that threshold removing the replica first. The experiment requires a matching timeout log after the fault starts and waits for asynchronous connection cleanup; a fixed sleep alone is not treated as proof.

While the replica is paused and recovers, a separate probe connects to the master and checks `PING` plus a sentinel `GET`. Resource observations include RSS, thread count, and open file descriptors. Probe duration includes connection, command, and resource-observation work. These sparse samples do not establish production availability or load capacity.

## Failure handling and evidence

The backpressure experiment has a total deadline. Cleanup first resumes an owned stopped replica, then stops both nodes and records their exit codes. The replica's resumed state matters: sending only a termination signal to a stopped process is not sufficient for prompt cleanup.

Each run retains configuration, server logs, a binary SHA256, stage results, and node exit codes. The repository contains only selected sanitized records; generated databases, complete local logs, build outputs, and caches remain local. Sanitization removes machine-specific paths and identifiers without changing statuses, counts, workload settings, or measured values.

The 2026-11-17 publication verification passed all nine experiment stages using the existing server build. The paused-replica experiment verified recovery at offset 133 and a subsequent write at offset 134; 68 master probes succeeded with no failures. Consult the [validation notes](evidence.md) and [validation.json](../../examples/reliability/validation.json) for the published run identity and limits.

## Interpretation limits

- WAL synchronous writes are enabled in these experiments, but killing a process does not model loss of host power, a faulty storage device, or a distributed durability guarantee.
- Replication is asynchronous. A successful client write is not evidence that a replica has applied that write.
- Catch-up success is checked with data and offsets. The experiment does not classify every recovery as full or incremental synchronization.
- No automatic failover, consensus algorithm, or replication protocol was implemented by this project.
- A real timeout log containing `sent 0` is distinct from the focused regression proving a blocking-write deadline bypass.
- TLS, Go integration tests, other operating systems, and throughput/latency optimization claims are outside the recorded scope.

## Review the code

Start with the [production diff explanation](socket-timeout.md), then read the [contribution map](contributions.md). Run the focused tests before the complete suite if investigating the socket behavior.
