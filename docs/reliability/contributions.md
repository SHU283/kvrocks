# Contribution map

This branch presents a focused reliability change and its validation against Apache Kvrocks. The database implementation belongs to the upstream project. No upstream acceptance or pull-request merge is claimed.

## Baseline and review commands

The development baseline is upstream commit `baa1da67f2ef94ffd62823116537ab7e95e7d5ba`. To inspect this branch's changes from the repository root:

```bash
git log --oneline baa1da67f2ef94ffd62823116537ab7e95e7d5ba..HEAD
git diff --stat baa1da67f2ef94ffd62823116537ab7e95e7d5ba..HEAD
git diff baa1da67f2ef94ffd62823116537ab7e95e7d5ba..HEAD -- \
  src/common/io_util.cc tests/cppunit/io_util_test.cc

# The focused production fix and four regression tests.
git show 871a0ac65fd01d033b0059c28942986f60f9f8c2
```

The first command exposes the individual commits; the last command isolates the C++ production and regression changes from documentation and experiment tooling. All paths below are relative to this repository and work on the published branch.

## Added or changed in this branch

| File or area | Contribution | Boundary |
|---|---|---|
| [`src/common/io_util.cc`](../../src/common/io_util.cc), positive-timeout non-TLS `SockSendWithTimeout` | Replace blocking `write` with per-call `send(MSG_DONTWAIT)`; retry `EINTR` from wait and send | Existing deadline, offset progression, and `EAGAIN` handling are retained |
| [`tests/cppunit/io_util_test.cc`](../../tests/cppunit/io_util_test.cc) | Four real-socket regression cases, test fixture, signal and thread cleanup | The complete C++ suite also includes unchanged upstream tests |
| [`tools/reliability/run_replication_lab.py`](../../tools/reliability/run_replication_lab.py) | Loopback RESP client, isolated node lifecycle, five-stage process-recovery experiment | Uses Kvrocks' existing replication and recovery mechanisms |
| [`tools/reliability/run_backpressure_lab.py`](../../tools/reliability/run_backpressure_lab.py) | Four-stage `SIGSTOP/SIGCONT` fault experiment, bounded data generation, timeout evidence, probes and recovery checks | Does not implement failover or claim a production-scale benchmark |
| [`tools/reliability/reproduce_socket.py`](../../tools/reliability/reproduce_socket.py) and [`socket-tests/CMakeLists.txt`](../../tools/reliability/socket-tests/CMakeLists.txt) | Build the original and patched implementation in separate directories and check their expected outcomes | Compiles the real production translation unit, not a mock |
| [`tools/reliability/run.py`](../../tools/reliability/run.py) and [`build_identity.py`](../../tools/reliability/build_identity.py) | Unified execution, source/build consistency checks, and result collection using an explicit build directory | Reuses an existing build; does not claim a fresh or hermetic build |
| README, these documents, and [`examples/reliability/validation.json`](../../examples/reliability/validation.json) | Scope, design rationale, reproducible commands, and selected sanitized evidence | No local credentials, generated databases, or private project materials are required |

The production patch is deliberately small: three added lines and one removed line in `io_util.cc`. The added regression code is 204 lines in the reviewed development diff, including test setup and helper logic. These counts describe scope; they are not used as a quality or productivity metric.

## Mechanisms supplied by Apache Kvrocks

Upstream supplies the Redis-compatible protocol and command implementation, libevent-based workers, RocksDB integration, WAL generation, replication negotiation, replication threads, checkpoint/full synchronization, and state-machine recovery. The project reads and exercises these components but does not claim to have implemented them.

Within `SockSendWithTimeout`, upstream already supplied `steady_clock` elapsed-time accounting, a shared timeout budget, partial-write byte tracking, and retry handling for a full socket buffer. The new code makes those mechanisms effective across the specific blocking-write and interrupted-call cases addressed here.

## What the evidence supports

- The old implementation fails a controlled real-socket deadline test with the designated watchdog diagnostic.
- The patched implementation passes that case and the added normal-data, interruption, and slow-reader regressions in the recorded environment.
- The real-node experiments check data values, updates, deletions, applied offsets, timeout logs, connection cleanup, and continued replication after recovery.
- The 2026-11-17 revalidation recorded 680 C++ tests passed, one skipped, zero failed; the patched control passed all five focused cases; the two real-node experiments passed nine stages.
- Saved logs and checksums associate results with a specific existing build. Tests were rerun, but the server and full unit-test binary were not rebuilt for that run. See [evidence and limitations](evidence.md).

The real backpressure experiment does not itself isolate the old blocking-write bug: its recorded timeout reports `sent 0`. Its value is the separately observed service-level timeout and recovery behavior. It does not establish a speedup, automatic failover, linearizability, or power-loss safety.

## Attribution and publication

The Apache Kvrocks copyright statements, `LICENSE`, and `NOTICE` remain applicable and unchanged. Existing upstream source is retained. Local snapshot-import history, development binaries, dependency archives, database files, and unrelated documents are not part of the personal contribution presented here.

The contribution is the reviewable patch, tests, experiment tooling, and documentation identified above. The publication records should be used to locate the exact personal commits and their base. There is no claim of an upstream issue, submitted pull request, or merge for this patch.
