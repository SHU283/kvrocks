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

"""Run C++ regressions and real-node reliability experiments against an existing build."""

import argparse
import contextlib
import datetime
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import tempfile
import time
import traceback

from build_identity import validate_build


def execute(report, run_dir, name, command, cwd, timeout, allow_failure=False):
    log_path = run_dir / f"{name}.log"
    record = {"name": name, "command": command, "log": str(log_path), "status": "running"}
    report["checks"].append(record)
    started = time.monotonic()
    print(f"RUN {name} (log: {log_path})", flush=True)
    with log_path.open("wb") as output:
        process = subprocess.Popen(command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            record["exit_code"] = process.wait(timeout=timeout)
        except BaseException:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGCONT)
                os.killpg(process.pid, signal.SIGTERM)
            if process.poll() is None:
                try:
                    process.wait(timeout=50)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            # The leader may exit before a descendant; clean up the owned group too.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            record["status"] = "interrupted_or_timed_out"
            raise
        finally:
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
    if record["exit_code"] != 0:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        record["status"] = "failed"
        if allow_failure:
            return record, log_path
        raise RuntimeError(f"{name} failed; inspect {log_path}")
    record["status"] = "passed"
    print(f"PASS {name}", flush=True)
    return record, log_path


def read_result(directory, expected_stages, expected_binary=None):
    candidates = list(directory.glob("*/result.json"))
    if len(candidates) != 1:
        raise RuntimeError(f"Expected exactly one result below {directory}")
    detail = json.loads(candidates[0].read_text(encoding="utf-8"))
    if detail.get("status") != "passed":
        raise RuntimeError(f"Experiment did not pass: {candidates[0]}")
    steps = detail.get("steps", [])
    if len(steps) != expected_stages or any(item.get("status") != "passed" for item in steps):
        raise RuntimeError(f"Incomplete experiment stages: {candidates[0]}")
    if expected_binary is not None and detail.get("binary_sha256") != expected_binary:
        raise RuntimeError(f"Experiment used an unexpected binary: {candidates[0]}")
    return candidates[0], detail


def run(args):
    project = Path(__file__).resolve().parents[2]
    if platform.system() != "Linux":
        raise ValueError("Use Linux/WSL to run this suite")
    build_dir = args.build_dir.expanduser().resolve()
    destination = args.output_dir.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(tempfile.mkdtemp(prefix=f"run-{stamp}-", dir=destination))
    tools_dir = project / "tools/reliability"
    report = {
        "schema_version": 1,
        "started_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status": "running", "platform": platform.platform(), "checks": [],
        "scope": "Linux plain-socket behavior, asynchronous replication and process recovery",
        "not_covered": ["fresh full build", "Go integration suite", "TLS transport", "power loss",
                        "automatic failover", "linearizability", "throughput benchmark", "other operating systems"],
    }
    print(f"Artifacts: {run_dir}", flush=True)
    try:
        print("CHECK existing build source, compile commands, mtimes and binary hashes", flush=True)
        identity = validate_build(project, build_dir)
        report["build_identity"] = identity
        record, log_path = execute(report, run_dir, "cpp_tests",
                                  [sys.executable, "x.py", "test", "cpp", str(build_dir)], project, 600,
                                  allow_failure=True)
        content = log_path.read_text(encoding="utf-8", errors="replace")
        totals = re.search(r"\[==========\] (\d+) tests from (\d+) test suites ran", content)
        passed = re.search(r"\[  PASSED  \] (\d+) tests", content)
        skipped = re.search(r"\[  SKIPPED \] (\d+) tests?, listed below", content)
        if not totals or not passed:
            raise RuntimeError("Missing complete C++ test summary")
        record.update(total=int(totals[1]), suites=int(totals[2]), passed=int(passed[1]),
                      skipped=int(skipped[1]) if skipped else 0)
        record["failed"] = record["total"] - record["passed"] - record["skipped"]
        if record["failed"] != 0 or record["exit_code"] != 0:
            raise RuntimeError("C++ test suite failed; see counts and log")
        controls = run_dir / "socket-reproduction"
        record, _ = execute(report, run_dir, "socket_regression_control",
                            [sys.executable, str(tools_dir / "reproduce_socket.py"),
                             "--build-dir", str(build_dir), "--work-dir", str(controls)], project, 600)
        result_path, _ = read_result(controls, 2)
        record["result"] = str(result_path)
        for name, script, stages in (("replication", "run_replication_lab.py", 5),
                                     ("backpressure", "run_backpressure_lab.py", 4)):
            output_dir = run_dir / name
            record, _ = execute(report, run_dir, name,
                                [sys.executable, str(tools_dir / script), "--binary", str(build_dir / "kvrocks"),
                                 "--work-dir", str(output_dir)], project, 300)
            result_path, _ = read_result(output_dir, stages, identity["binaries"]["kvrocks"]["sha256"])
            record["result"] = str(result_path)
            record["stages_passed"] = stages
        if validate_build(project, build_dir) != identity:
            raise RuntimeError("Build identity changed during verification")
        report["status"] = "passed"
    except BaseException as error:
        if report["checks"]:
            report["checks"][-1]["status"] = "failed"
        report.update(status="failed", error=f"{type(error).__name__}: {error}")
        traceback.print_exc()
    finally:
        report["finished_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        result = run_dir / "result.json"
        result.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        lines = ["# Reliability verification", "", f"Status: **{report['status']}**", "",
                 "An existing build was inspected and reused. No fresh full build was performed by this command.",
                 "", "| Check | Status | Evidence |", "|---|---|---|"]
        for check in report["checks"]:
            evidence = f"[log]({Path(check['log']).name})"
            if "result" in check:
                evidence += f" / [result]({Path(check['result']).relative_to(run_dir).as_posix()})"
            lines.append(f"| {check['name']} | {check['status']} | {evidence} |")
        lines += ["", "Commands, source matching inventory, binary hashes, counts and timing: [result.json](result.json)",
                  "", "Not covered: " + ", ".join(report["not_covered"])]
        if "error" in report:
            lines += ["", "Error: " + report["error"]]
        (run_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"{report['status'].upper()}: {result}", flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    project = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=project / "build",
                        help="Existing Kvrocks build, including unittest and compile_commands.json")
    parser.add_argument("--output-dir", type=Path, default=project / "artifacts/reliability")

    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"Received signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    sys.exit(run(parser.parse_args()))

