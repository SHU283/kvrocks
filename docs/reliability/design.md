# Replication fault experiments

The validation combines real-socket C++ regressions with two Kvrocks processes. The scripts use Python's standard library and a small RESP client.

## Socket regressions

The four added cases cover a blocked sender deadline, binary data and descriptor flags, repeated `SIGUSR1` interruption, and partial writes to a slow reader. The before/after control compiles the original and patched implementation with these tests. See the [socket analysis](socket-timeout.md).

## Process recovery

```bash
python3 tools/reliability/run_replication_lab.py --binary build/kvrocks --keys 100
```

| Stage | Operation | Check |
|---|---|---|
| Initial writes | Start master and write 100 keys | Expected values on master |
| Initial replication | Start replica | Link up, expected values, applied offset reaches target |
| Replica crash and catch-up | Kill replica; add a batch, update and delete a key; restart | New values, update, deletion and offset |
| Master crash and persistence | Kill and restart master using its data directory | Values and deletion survive the process restart |
| Replication after restart | Write a third batch | 300 expected keys including deletion and the final offset |

Each node receives an already-bound loopback listener, avoiding a port-allocation race. Startup checks the child PID reported by `INFO server`. The experiment uses asynchronous replication and synchronous WAL writes; the injected fault is process termination with `SIGKILL`.

## Paused-replica backpressure

```bash
python3 tools/reliability/run_backpressure_lab.py --binary build/kvrocks --payload-mib 32 --send-timeout-ms 1000
```

| Stage | Operation | Check |
|---|---|---|
| Initial synchronization | Seed three keys and start replica | Data and offset agree |
| Paused replica | `SIGSTOP`; write 128 values of 256 KiB, update and delete a key | Stopped process, send-timeout log and connection cleanup |
| Resume and recover | `SIGCONT` | All 131 expected keys including deletion and target offset |
| Continue replication | Write one further key | New data reaches replica; master probe results collected |

Payloads use a fixed random seed. The workload defaults to 32 MiB and accepts 16-64 MiB. The master sets `replication-send-timeout-ms` before connecting the replica. `max-replication-lag=0` disables the separate lag threshold so the send timeout controls this experiment.

A separate probe checks `PING` and a sentinel `GET` on the master during the pause and recovery. Samples include connection and command time, RSS, thread count and open file descriptors. The script waits for an actual timeout log and connection cleanup before resuming the replica.

## Execution and cleanup

The backpressure experiment has an overall deadline. Cleanup resumes a stopped replica before terminating both nodes, and records their exit codes. The parent runner can terminate the owned process group if a child command times out.

Results include stage outcomes, data/offset checks, timings and node exit codes. Generated configurations, server logs and databases remain under `artifacts/reliability/`. The [recorded run](evidence.md) passed all nine stages.
