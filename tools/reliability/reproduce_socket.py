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

"""Rebuild the original socket implementation and demonstrate the regression."""

import argparse
import contextlib
import datetime
import json
import os
import platform
from pathlib import Path
import signal
import subprocess
import tempfile
import sys

# Baseline implementation used by the before/after regression control.
UPSTREAM_SHA = "baa1da67f2ef94ffd62823116537ab7e95e7d5ba"


def run_command(command, output, timeout):
    process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        return process.wait(timeout=timeout)
    except BaseException:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    project = Path(__file__).resolve().parents[2]
    parser.add_argument("--build-dir", type=Path, default=project / "build")
    parser.add_argument("--work-dir", type=Path, default=project / "artifacts/reliability/socket-reproduction")
    args = parser.parse_args()
    if platform.system() != "Linux":
        parser.error("Run inside Linux/WSL")
    destination = args.work_dir
    destination.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="socket-", dir=destination.resolve()))
    source = project
    native_build = args.build_dir.expanduser().resolve()
    if not (native_build / "_deps").is_dir():
        parser.error("--build-dir must contain expanded Kvrocks dependencies in _deps")
    # Compilers perform better on the Linux filesystem; logs remain in the project.
    scratch = Path(tempfile.mkdtemp(prefix="kvrocks-socket-control-"))
    baseline = subprocess.check_output(
        ["git", "show", f"{UPSTREAM_SHA}:src/common/io_util.cc"], cwd=source)
    baseline_file = scratch / "io_util_before.cc"
    baseline_file.write_bytes(baseline)
    report = {"status": "running", "started_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "build_directory": str(scratch), "steps": []}
    try:
        for name, implementation in (("before", baseline_file), ("after", source / "src/common/io_util.cc")):
            build_dir = scratch / name
            with (run_dir / f"{name}-build.log").open("wb") as log:
                configure = ["cmake", "-S", str(project / "tools/reliability/socket-tests"), "-B", str(build_dir),
                             "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Debug", f"-DSOCKET_IO_SOURCE={implementation}",
                             f"-DKVROCKS_SOURCE={source}", f"-DKVROCKS_DEPS={native_build / '_deps'}"]
                if run_command(configure, log, 120) != 0:
                    raise RuntimeError(f"{name} configure failed")
                if run_command(["cmake", "--build", str(build_dir), "-j2"], log, 180) != 0:
                    raise RuntimeError(f"{name} build failed")
            command = [str(build_dir / "socket_regression")]
            if name == "before":
                command.append("--gtest_filter=IOUtilSocketTest.SendWithTimeoutHonorsDeadlineOnBlockingSocket")
            log_path = run_dir / f"{name}.log"
            with log_path.open("wb") as log:
                returncode = run_command(command, log, 20)
            output = log_path.read_text(encoding="utf-8", errors="replace")
            expected = (returncode == 1 and
                        "[  FAILED  ] IOUtilSocketTest.SendWithTimeoutHonorsDeadlineOnBlockingSocket" in output
                        and "Socket send ignored its deadline and needed watchdog cleanup" in output
                        if name == "before" else returncode == 0 and "[  PASSED  ] 5 tests." in output)
            report["steps"].append({"name": name, "exit_code": returncode,
                                    "expected_failure": name == "before", "status": "passed" if expected else "failed"})
            if not expected:
                raise RuntimeError(f"Unexpected {name} result; see {run_dir / (name + '.log')}")
            print(f"PASS {name}: {'original regression reproduced' if name == 'before' else 'patched tests passed'}", flush=True)
        report["status"] = "passed"
    except BaseException as error:
        report.update(status="failed", error=f"{type(error).__name__}: {error}")
    finally:
        report["finished_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        result_path = run_dir / "result.json"
        result_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{report['status'].upper()}: {result_path}", flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"Received signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    sys.exit(main())
