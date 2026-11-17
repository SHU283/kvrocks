#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Observe a real Kvrocks replication send timeout and recovery from a paused peer.

This bounded Linux loopback experiment uses SIGSTOP/SIGCONT on its own replica.
Timing and resource samples describe this run; they are not throughput benchmarks.
"""

import argparse
import contextlib
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import signal
import sys
import tempfile
import threading
import time
import traceback

from run_replication_lab import (
    Node, require_equal, step, verify_values, wait_caught_up, wait_for,
)


def resource_sample(node):
    node.check_running()
    root = Path(f"/proc/{node.process.pid}")
    status = dict(line.split(":", 1) for line in (root / "status").read_text().splitlines())
    return {
        "pid": node.process.pid,
        "state": status["State"].strip(),
        "rss_kib": int(status["VmRSS"].split()[0]),
        "high_water_rss_kib": int(status["VmHWM"].split()[0]),
        "threads": int(status["Threads"]),
        "open_fds": len(list((root / "fd").iterdir())),
    }


class AvailabilityProbe:
    """Keep independent bounded PING/GET samples while writes or recovery run."""

    def __init__(self, node, output):
        self.node = node
        self.output = output
        self.samples = []
        self.phase = "before_pause"
        self.stopping = threading.Event()
        self.started = time.monotonic()
        self.thread = threading.Thread(target=self.loop, name="master-availability-probe")

    def loop(self):
        with self.output.open("w", encoding="utf-8") as stream:
            while not self.stopping.is_set():
                started = time.monotonic()
                sample = {"elapsed_seconds": round(started - self.started, 4), "phase": self.phase}
                try:
                    with self.node.client() as client:
                        require_equal(client.command("PING"), b"PONG", "master PING")
                        require_equal(client.command("GET", "backpressure:sentinel"), b"alive", "sentinel")
                    sample.update(status="passed", resources=resource_sample(self.node))
                except Exception as error:
                    sample.update(status="failed", error=f"{type(error).__name__}: {error}")
                sample["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
                self.samples.append(sample)
                stream.write(json.dumps(sample) + "\n")
                stream.flush()
                self.stopping.wait(0.1)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stopping.set()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("Availability probe did not stop within its socket deadline")


def timeout_evidence(node, start_offset):
    log_path = Path(node.history[-1]["log"])
    with log_path.open("rb") as stream:
        stream.seek(start_offset)
        lines = stream.read().decode("utf-8", errors="replace").splitlines()
    return [line for line in lines if "Write error while sending batch to slave" in line and "send timeout" in line]


def run(args):
    if platform.system() != "Linux":
        raise ValueError("Run inside Linux/WSL")
    binary = args.binary.expanduser().resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError(f"Executable Kvrocks binary not found: {binary}")
    args.work_dir.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="backpressure-", dir=args.work_dir.resolve()))
    report = {
        "schema_version": 1,
        "started_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status": "running",
        "binary": str(binary),
        "platform": platform.platform(),
        "run_directory": str(run_dir),
        "configuration": {
            "payload_mib": args.payload_mib, "value_bytes": 256 * 1024,
            "payload_seed": 20261111, "send_timeout_ms": args.send_timeout_ms,
            "max_replication_lag": 0, "wal_sync": True,
            "replication": "asynchronous", "workers_per_node": 2,
            "wait_timeout_seconds": args.timeout, "total_timeout_seconds": args.total_timeout,
        },
        "scope": "Real loopback replication with a SIGSTOP peer; no performance or production availability guarantee",
        "steps": [],
    }
    with binary.open("rb") as stream:
        report["binary_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    master = Node("master", binary, run_dir, args.timeout)
    replica = Node("replica", binary, run_dir, args.timeout, upstream=master)
    paused = False
    probe = None
    expected = {"backpressure:sentinel": b"alive", "backpressure:update": b"before", "backpressure:delete": b"before"}
    print(f"Artifacts: {run_dir}", flush=True)
    signal.setitimer(signal.ITIMER_REAL, args.total_timeout)
    try:
        with step(report, "initial_replication") as record:
            master.start()
            with master.client() as client:
                # FeedSlaveThread snapshots this setting when its connection is created.
                for key, value in (("replication-send-timeout-ms", args.send_timeout_ms), ("max-replication-lag", 0)):
                    require_equal(client.command("CONFIG", "SET", key, value), b"OK", f"CONFIG SET {key}")
                    require_equal(client.command("CONFIG", "GET", key), [key.encode(), str(value).encode()], f"CONFIG GET {key}")
                for key, value in expected.items():
                    require_equal(client.command("SET", key, value), b"OK", f"SET {key}")
            replica.start()
            record.update(wait_caught_up(master, replica, expected, args.timeout))
            record["master_resources"] = resource_sample(master)
            probe = AvailabilityProbe(master, run_dir / "availability.jsonl")
            probe.start()
        with step(report, "paused_replica_send_timeout") as record:
            log_offset = Path(master.history[-1]["log"]).stat().st_size
            replica.process.send_signal(signal.SIGSTOP)
            paused = True
            wait_for(lambda: resource_sample(replica)["state"].startswith("T"), 3, "replica stopped state")
            record["replica_stopped_resources"] = resource_sample(replica)
            probe.phase = "replica_paused"
            paused_at = time.monotonic()
            generator = random.Random(20261111)
            value_size = report["configuration"]["value_bytes"]
            key_count = args.payload_mib * 1024 * 1024 // value_size
            write_started = time.monotonic()
            with master.client() as client:
                for index in range(key_count):
                    if time.monotonic() - write_started >= args.timeout:
                        raise TimeoutError("Bounded payload write deadline exceeded")
                    key = f"backpressure:payload:{index}"
                    value = generator.randbytes(value_size)
                    require_equal(client.command("SET", key, value), b"OK", f"SET {key}")
                    expected[key] = value
                require_equal(client.command("SET", "backpressure:update", "after"), b"OK", "update")
                require_equal(client.command("DEL", "backpressure:delete"), 1, "delete")
            expected["backpressure:update"] = b"after"
            expected["backpressure:delete"] = None
            record["payload_keys_written"] = key_count
            record["payload_bytes_written"] = key_count * value_size
            record["write_duration_seconds"] = round(time.monotonic() - write_started, 4)
            wait_for(lambda: timeout_evidence(master, log_offset), args.timeout, "actual replication send-timeout log")
            record["send_timeout_log_lines"] = timeout_evidence(master, log_offset)
            record["pause_to_observed_timeout_seconds"] = round(time.monotonic() - paused_at, 4)
            record["master_resources_after_timeout"] = resource_sample(master)
            wait_for(lambda: master.replication_info()["connected_slaves"] == "0", args.timeout,
                     "asynchronous removal of timed-out replica")
            record["master_replication_after_timeout"] = master.replication_info()
            verify_values(master, expected)
        with step(report, "resume_and_verify_recovery") as record:
            probe.phase = "replica_recovering"
            resumed_at = time.monotonic()
            replica.process.send_signal(signal.SIGCONT)
            paused = False
            record.update(wait_caught_up(master, replica, expected, args.timeout))
            record["resume_to_data_verified_seconds"] = round(time.monotonic() - resumed_at, 4)
            record["checked_keys_including_deleted"] = len(expected)
            record["master_resources_after_recovery"] = resource_sample(master)
            record["replica_resources_after_recovery"] = resource_sample(replica)
        with step(report, "replication_continues_after_recovery") as record:
            probe.phase = "recovered"
            with master.client() as client:
                require_equal(client.command("SET", "backpressure:after_recovery", "replicated"), b"OK", "new write")
            expected["backpressure:after_recovery"] = b"replicated"
            record.update(wait_caught_up(master, replica, expected, args.timeout))
            probe.stop()
            failures = [sample for sample in probe.samples if sample["status"] != "passed"]
            record["availability_samples"] = len(probe.samples)
            record["availability_failures"] = len(failures)
            require_equal(len(failures), 0, "independent master PING/GET failures")
            if not any(sample["phase"] == "replica_paused" for sample in probe.samples):
                raise AssertionError("No availability sample was collected while the replica was paused")
        report["status"] = "passed"
    except BaseException as error:
        report.update(status="failed", error=f"{type(error).__name__}: {error}")
        traceback.print_exc()
    finally:
        # Disable the experiment alarm before bounded cleanup of owned processes.
        signal.setitimer(signal.ITIMER_REAL, 0)
        errors = []
        if paused and replica.process is not None and replica.process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                replica.process.send_signal(signal.SIGCONT)
        if probe is not None:
            try:
                probe.stop()
            except Exception as error:
                errors.append(f"probe: {error}")
            passed = [sample for sample in probe.samples if sample["status"] == "passed"]
            report["availability"] = {
                "sample_count": len(probe.samples), "passed": len(passed),
                "failed": len(probe.samples) - len(passed), "samples_file": "availability.jsonl",
                "max_observed_probe_ms": max((sample["duration_ms"] for sample in probe.samples), default=None),
                "max_sampled_master_rss_kib": max((sample["resources"]["rss_kib"] for sample in passed), default=None),
                "note": "One PING/GET connection per sample, including connect time; sparse observations, not a load benchmark",
            }
        for node in (replica, master):
            try:
                node.stop()
            except Exception as error:
                errors.append(f"{node.name}: {error}")
        if errors:
            report.update(status="failed", cleanup_errors=errors)
        report["nodes"] = {node.name: {"port": node.port, "processes": node.history} for node in (master, replica)}
        report["finished_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        output = run_dir / "result.json"
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{report['status'].upper()}: {output}", flush=True)
    return 0 if report["status"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--work-dir", type=Path, default=Path(__file__).resolve().parent / "results")
    parser.add_argument("--payload-mib", type=int, default=32, help="Bounded payload size, 16 to 64 MiB")
    parser.add_argument("--send-timeout-ms", type=int, default=1000)
    parser.add_argument("--timeout", type=float, default=60, help="Deadline for each wait or payload write phase")
    parser.add_argument("--total-timeout", type=float, default=180)
    args = parser.parse_args()
    if not 16 <= args.payload_mib <= 64:
        parser.error("--payload-mib must be between 16 and 64")
    if not 1000 <= args.send_timeout_ms <= 10000:
        parser.error("--send-timeout-ms must be between 1000 and 10000")
    if any(not math.isfinite(value) or value <= 0 for value in (args.timeout, args.total_timeout)):
        parser.error("Timeouts must be finite and positive")
    if platform.system() != "Linux":
        parser.error("Run inside Linux/WSL")

    def interrupted(signum, frame):
        if signum == signal.SIGALRM:
            raise TimeoutError("Experiment total deadline exceeded")
        raise KeyboardInterrupt(f"Received signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGALRM, interrupted)
    try:
        return run(args)
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
