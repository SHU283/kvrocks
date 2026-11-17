# Blocking socket writes can bypass an outer send deadline

This analysis applies to the positive-timeout, non-TLS overload of `util::SockSendWithTimeout`.

## The call path

[`CommandPSync::Execute`](../../src/commands/cmd_replication.cc) detaches an eligible replication connection and sets its socket to blocking mode. [`FeedSlaveThread::loop`](../../src/cluster/replication.cc) reads batches from the RocksDB WAL and calls [`SockSendWithTimeout`](../../src/common/io_util.cc) to send the replication stream.

The original function already tracked elapsed time with `steady_clock`, computed the remaining budget, called `AeWait` (a `poll` helper), advanced the byte offset after partial writes, and retried `EAGAIN`/`EWOULDBLOCK`. However, the actual send used blocking `write`:

```cpp
int ready = AeWait(fd, AE_WRITABLE, remaining_ms);
// ... handle the poll result ...
ssize_t nwritten = write(fd, data.data() + n, data.size() - n);
```

Writability indicates that some progress is possible at that moment. It does not reserve enough buffer space for the entire remaining payload, nor does the `poll` timeout limit the duration of the following `write`. If the receiver stops consuming data, the thread can remain inside the blocking call and never return to the outer deadline check.

## The production patch

Only the socket call and interruption handling change:

```diff
 if (ready < 0) {
+  if (errno == EINTR) continue;
   return Status::FromErrno("poll error while sending");
 }

-ssize_t nwritten = write(fd, data.data() + n, data.size() - n);
+ssize_t nwritten = send(fd, data.data() + n, data.size() - n, MSG_DONTWAIT);
 if (nwritten == -1) {
+  if (errno == EINTR) continue;
   if (errno == EAGAIN || errno == EWOULDBLOCK) {
     continue;
   }
```

`MSG_DONTWAIT` applies to this send call. It lets the function return to the existing loop when more progress cannot be made immediately. The loop still advances `n` after a short send, waits for writability when appropriate, and calculates the remaining budget from the original start time.

Retrying `EINTR` prevents a delivered signal from being treated as an immediate transport failure. Retrying does not start a new timeout budget.

## Why not change the descriptor flags?

Changing `O_NONBLOCK` changes the file status flags associated with the open file description and can affect other users of that socket. A per-call flag addresses the specific send operation without changing the connection's existing blocking configuration. The normal-send regression checks both initially blocking and initially nonblocking descriptors, including their flags after the call.

The nonpositive-timeout path and TLS overload retain their existing behavior.

## Regression evidence

The timeout test uses a real `socketpair`, a requested 4096-byte send buffer, a 1 MiB payload, a receiver that does not read, and a 100 ms send timeout. An independent watchdog shuts down the receiver after two seconds so an unfixed implementation cannot indefinitely hold up the test process. Watchdog activation is a failure, not a successful timeout result.

The automatic before/after control compiles the original and patched `io_util.cc` translation units separately with the same tests, headers, and dependency versions. The production differences in that translation unit are limited to the changes shown above. For the original implementation, the control requires both the named test failure and this diagnostic:

```text
Socket send ignored its deadline and needed watchdog cleanup
```

This avoids treating an unrelated socket setup failure as a successful reproduction. The patched run must pass all five focused cases: the four added tests and the existing listening-address test.

The other new cases check binary data containing `NUL`, repeated thread-directed `SIGUSR1` interruptions while the send buffer is full, and a 256 KiB binary payload sent through a small buffer to a delayed reader. The signal test targets interrupted `poll`; both `poll` and `send` retry branches are included in the patch. The signal test restores the previous handler; the tests join their worker threads and release their descriptors.

See the [recorded results](evidence.md) and [replication experiment design](design.md).
