# Build and run

Run from this branch's repository root on Linux or WSL2. Use a Git checkout: the before/after control reads the original implementation from its upstream baseline.

## Build

The tools use Python 3.11 or newer and the standard library. Kvrocks requires a C++20 compiler, CMake and the dependencies downloaded by its build script.

```bash
sudo apt-get install build-essential cmake ninja-build python3 git autoconf automake libtool pkg-config
python3 x.py build --ninja --unittest -j4
```

Reduce `-j4` on hosts with limited memory. The build directory needs the `kvrocks` and `unittest` executables plus `_deps` for the socket control.

## Complete test run

```bash
python3 tools/reliability/run.py --build-dir build
```

This runs the C++ suite, the original-versus-patched socket control, the five-stage process recovery experiment and the four-stage backpressure experiment. Use `--build-dir` for another build location and `--output-dir` for another output directory.

Each run creates a unique directory under `artifacts/reliability/` with a summary, results and command logs. A failed check returns a nonzero exit code.

## Focused checks

```bash
# Four new socket cases and the existing listening-address case.
python3 x.py test cpp build --gtest_filter='IOUtil.*:IOUtilSocketTest.*'

# Original implementation versus the patched implementation.
python3 tools/reliability/reproduce_socket.py --build-dir build

# Process crashes and replication recovery.
python3 tools/reliability/run_replication_lab.py --binary build/kvrocks --keys 100 --timeout 60

# A paused replica, 32 MiB of writes, timeout and catch-up.
python3 tools/reliability/run_backpressure_lab.py --binary build/kvrocks --payload-mib 32 --send-timeout-ms 1000
```

The socket control compiles both implementations with the same regression tests and expanded dependencies. Its temporary build directories remain available for inspection.

The real-node scripts allocate loopback ports, create separate data directories and clean up their own child processes. Allow enough disk and memory for the build, two server processes and retained output. See the [experiment design](design.md) and [recorded results](evidence.md).
