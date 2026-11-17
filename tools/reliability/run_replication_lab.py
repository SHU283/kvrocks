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

"""Run an isolated, repeatable Kvrocks replication and process-recovery lab.

Uses only Python's standard library. All generated files are retained in a new
run directory.
"""

import argparse
import contextlib
import datetime
import json
import os
from pathlib import Path
import platform
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback


class RedisError(RuntimeError):
    pass


def read_reply(stream):
    line = stream.readline(65537)
    if not line.endswith(b"\r\n"):
        raise ConnectionError("Incomplete or oversized RESP line")
    kind, value = line[:1], line[1:-2]
    if kind == b"+":
        return value
    if kind == b"-":
        raise RedisError(value.decode("utf-8", errors="replace"))
    if kind == b":":
        return int(value)
    if kind == b"$":
        length = int(value)
        if length == -1:
            return None
        if not 0 <= length <= 16 * 1024 * 1024:
            raise ValueError("Unexpected RESP bulk length")
        data = stream.read(length + 2)
        if len(data) != length + 2 or data[-2:] != b"\r\n":
            raise ConnectionError("Incomplete RESP bulk value")
        return data[:-2]
    if kind == b"*":
        count = int(value)
        if count == -1:
            return None
        if not 0 <= count <= 10000:
            raise ValueError("Unexpected RESP array length")
        return [read_reply(stream) for _ in range(count)]
    raise ValueError(f"Unsupported RESP reply type: {kind!r}")


class Client:
    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=2)
        self.stream = self.sock.makefile("rb")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stream.close()
        self.sock.close()

    def command(self, *args):
        values = [arg if isinstance(arg, bytes) else str(arg).encode() for arg in args]
        request = b"*%d\r\n" % len(values)
        request += b"".join(b"$%d\r\n" % len(value) + value + b"\r\n" for value in values)
        self.sock.sendall(request)
        return read_reply(self.stream)

    def info(self, section):
        raw = self.command("INFO", section).decode("utf-8")
        return dict(line.split(":", 1) for line in raw.splitlines() if line and not line.startswith("#"))


def wait_for(predicate, timeout, description):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except (OSError, RedisError) as error:
            last_error = str(error)
        time.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for {description}; last error: {last_error}")


class Node:
    def __init__(self, name, binary, run_dir, timeout, upstream=None):
        self.name = name
        self.binary = binary
        self.directory = run_dir / name
        self.directory.mkdir()
        self.timeout = timeout
        self.upstream = upstream
        self.port = 0
        self.process = None
        self.log = None
        self.history = []

    def start(self):
        if self.process is not None and self.process.poll() is None:
            raise RuntimeError(f"{self.name} is already running")
        # Keep the listener bound until it is inherited, without SO_REUSEPORT.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", self.port))
            listener.listen(128)
            listener.setblocking(False)
            self.port = listener.getsockname()[1]
            config = {
                "bind": "127.0.0.1",
                "port": self.port,
                "socket-fd": listener.fileno(),
                "daemonize": "no",
                "workers": 2,
                "dir": json.dumps(str(self.directory), ensure_ascii=False),
                "log-dir": "stdout",
                "db-name": "kvrocks-replication-lab",
                "rocksdb.write_options.sync": "yes",
                "rocksdb.block_cache_size": 32,
                "rocksdb.write_buffer_size": 8,
                "rocksdb.max_background_jobs": 2,
            }
            if self.upstream:
                config["slaveof"] = f"127.0.0.1 {self.upstream.port}"
            config_path = self.directory / "kvrocks.conf"
            config_path.write_text("".join(f"{key} {value}\n" for key, value in config.items()), encoding="utf-8")
            log_path = self.directory / f"process-{len(self.history) + 1}.log"
            self.log = log_path.open("ab", buffering=0)
            try:
                self.process = subprocess.Popen(
                    [str(self.binary), "-c", str(config_path)],
                    cwd=self.directory,
                    stdin=subprocess.DEVNULL,
                    stdout=self.log,
                    stderr=subprocess.STDOUT,
                    pass_fds=(listener.fileno(),),
                    # Inherit the lab process group so its supervisor can clean up all nodes.
                    start_new_session=False,
                )
            except BaseException:
                self.log.close()
                self.log = None
                raise
            self.history.append({"pid": self.process.pid, "log": str(log_path)})

        def ready():
            self.check_running()
            with self.client() as client:
                if client.command("PING") != b"PONG":
                    return False
                info = client.info("server")
                if int(info["process_id"]) != self.process.pid:
                    raise RuntimeError("Connected to an unexpected server process")
                return True

        wait_for(ready, self.timeout, f"{self.name} startup (see {log_path})")

    def check_running(self):
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError(f"{self.name} exited unexpectedly; inspect {self.directory}")

    def client(self):
        self.check_running()
        return Client(self.port)

    def stop(self, crash=False):
        if self.process is None:
            return
        try:
            if self.process.poll() is None:
                if crash:
                    self.process.kill()
                else:
                    self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=10)
                    if not crash:
                        raise RuntimeError(f"{self.name} required forced cleanup")
            self.history[-1]["exit_code"] = self.process.returncode
        finally:
            if self.log is not None:
                self.log.close()
                self.log = None

    def replication_info(self):
        with self.client() as client:
            return client.info("replication")


def require_equal(actual, expected, description):
    if actual != expected:
        raise AssertionError(f"{description}: expected {expected!r}, got {actual!r}")


def write_batch(node, expected, phase, count):
    with node.client() as client:
        for index in range(count):
            key = f"lab:{phase}:{index}"
            value = f"{phase}-value-{index}".encode()
            require_equal(client.command("SET", key, value), b"OK", "SET response")
            expected[key] = value


def verify_values(node, expected):
    with node.client() as client:
        for key, value in expected.items():
            require_equal(client.command("GET", key), value, f"{node.name} GET {key}")


def wait_caught_up(master, replica, expected, timeout):
    target = int(master.replication_info()["master_repl_offset"])

    def caught_up():
        master.check_running()
        info = replica.replication_info()
        offset = int(info.get("slave_repl_offset", "-1"))
        return info.get("master_link_status") == "up" and offset >= target

    wait_for(caught_up, timeout, "replication link and applied offset")
    verify_values(replica, expected)
    return {"target_offset": target, "replica_info": replica.replication_info()}


@contextlib.contextmanager
def step(report, name):
    record = {"name": name, "status": "running"}
    report["steps"].append(record)
    started = time.monotonic()
    print(f"RUN  {name}", flush=True)
    try:
        yield record
    except BaseException as error:
        record.update(status="failed", error=f"{type(error).__name__}: {error}")
        raise
    else:
        record["status"] = "passed"
        print(f"PASS {name}", flush=True)
    finally:
        record["duration_seconds"] = round(time.monotonic() - started, 3)


def run(args):
    binary = args.binary.expanduser().resolve()
    if platform.system() != "Linux":
        raise ValueError("Run this script inside Linux/WSL; native Windows is not supported")
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError(f"Executable Kvrocks binary not found: {binary}")
    args.work_dir.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="replication-", dir=args.work_dir.resolve()))
    report = {
        "schema_version": 1,
        "started_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status": "running",
        "binary": str(binary),
        "platform": platform.platform(),
        "run_directory": str(run_dir),
        "keys_per_batch": args.keys,
        "configuration": {"wal_sync": True, "replication": "asynchronous", "workers_per_node": 2},
        "steps": [],
    }
    master = Node("master", binary, run_dir, args.timeout)
    replica = Node("replica", binary, run_dir, args.timeout, upstream=master)
    expected = {}
    print(f"Artifacts: {run_dir}", flush=True)
    try:
        with step(report, "master_start_and_read_write"):
            master.start()
            write_batch(master, expected, "initial", args.keys)
            verify_values(master, expected)
        with step(report, "initial_replication") as record:
            replica.start()
            record.update(wait_caught_up(master, replica, expected, args.timeout))
        with step(report, "replica_crash_and_catch_up") as record:
            replica.stop(crash=True)
            require_equal(replica.process.returncode, -signal.SIGKILL, "replica crash exit code")
            write_batch(master, expected, "offline", args.keys)
            with master.client() as client:
                require_equal(client.command("SET", "lab:initial:0", "updated"), b"OK", "update")
                require_equal(client.command("DEL", "lab:initial:1"), 1, "delete")
            expected["lab:initial:0"] = b"updated"
            expected["lab:initial:1"] = None
            replica.start()
            record.update(wait_caught_up(master, replica, expected, args.timeout))
        with step(report, "master_crash_and_persistence"):
            master.stop(crash=True)
            require_equal(master.process.returncode, -signal.SIGKILL, "master crash exit code")
            master.start()
            verify_values(master, expected)
        with step(report, "replication_after_master_restart") as record:
            write_batch(master, expected, "restarted", args.keys)
            record.update(wait_caught_up(master, replica, expected, args.timeout))
            record["checked_keys_including_deleted"] = len(expected)
        report["status"] = "passed"
    except BaseException as error:
        report.update(status="failed", error=f"{type(error).__name__}: {error}")
        traceback.print_exc()
    finally:
        errors = []
        for node in (replica, master):
            try:
                node.stop()
            except Exception as error:
                errors.append(f"{node.name}: {error}")
        if errors:
            report.update(status="failed", cleanup_errors=errors)
        report["nodes"] = {
            node.name: {"port": node.port, "processes": node.history}
            for node in (master, replica)
        }
        report["finished_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        output = run_dir / "result.json"
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{report['status'].upper()}: {output}", flush=True)
    return 0 if report["status"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path, help="Path to the compiled Linux kvrocks binary")
    parser.add_argument("--work-dir", type=Path, default=Path(__file__).resolve().parent / "results")
    parser.add_argument("--keys", type=int, default=100, help="Keys written per phase (at least 2)")
    parser.add_argument("--timeout", type=float, default=60, help="Seconds allowed for each readiness/recovery wait")
    args = parser.parse_args()
    if args.keys < 2 or args.timeout <= 0:
        parser.error("--keys must be at least 2 and --timeout must be positive")

    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"Received signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        return run(args)
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
