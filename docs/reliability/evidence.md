# Test and experiment results

The recorded run completed on **2026-11-17** using Ubuntu 24.04.3 / WSL2, GCC 13.3.0 and Python 3.12.3. It used the existing server and full unit-test build; the two small socket control targets were compiled separately.

| Check | Result |
|---|---|
| Full C++ suite | 681 tests in 87 suites: **680 passed, 1 skipped, 0 failed** |
| Original socket control | Deadline test hit the watchdog and failed as expected |
| Patched socket control | **5/5 passed**, including the four new regression cases |
| Process recovery experiment | **5/5 stages passed**; 300 expected keys including deletion, final offset 302 |
| Backpressure experiment | **4/4 stages passed**; 32 MiB in 128 values, recovery verified in 3.5452 seconds |
| Continued replication | Offset 133 after catch-up, then 134 after a new write |
| Master PING/GET probes | **68/68 succeeded** during the backpressure run |

The skipped test is upstream `UseBitmap/RedisBitmapTest.BitfieldStringGetSetTest/0`. All final node processes exited successfully; `SIGKILL` stages recorded the expected signal exit.

The fault workload uses asynchronous replication, synchronous WAL writes and local process crashes. Timings are observations from this run. The observed real-node timeout was `sent 0 of 262219 bytes`, followed by data recovery; the separate socket control isolates the blocking-write bug.

## Output

- [C++ test excerpt](../../examples/reliability/cpp-tests.excerpt.txt)
- [Before](../../examples/reliability/socket-before.txt) and [after](../../examples/reliability/socket-after.txt) socket output
- [Overall results](../../examples/reliability/validation.json) and [socket control](../../examples/reliability/socket-control.json)
- [Replication results](../../examples/reliability/replication.json)
- [Backpressure results](../../examples/reliability/backpressure.json) and [probe samples](../../examples/reliability/availability.jsonl)

Use the [reproduction commands](reproduction.md) to run the checks locally.
