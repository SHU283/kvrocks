# Reproduce the reliability checks

Run these commands from this branch's repository root on Linux or WSL2. Use a Git checkout containing upstream commit `baa1da67f2ef94ffd62823116537ab7e95e7d5ba`: the before/after control reads that exact version with `git show`. A source archive without Git history is insufficient for that control.

## Build requirements

The recorded environment is Ubuntu 24.04.3 on WSL2, GCC 13.3.0, CMake 3.28, Ninja, and Python 3.12.3. The Python tools require Python 3.11 or newer and use only the standard library. A C++20 compiler is required by Kvrocks. Network access is needed for upstream's pinned build dependencies.

```bash
sudo apt-get install build-essential cmake ninja-build python3 git autoconf automake libtool pkg-config
python3 x.py build --ninja --unittest -j4
```

The build must include `kvrocks`, `unittest`, `CMakeCache.txt`, `compile_commands.json`, and the expanded `_deps` directory. The upstream CMake configuration enables the compilation database. Reduce `-j4` if the host has limited memory. Keep the build and its original source tree accessible until verification finishes.

## Complete verification

```bash
python3 tools/reliability/run.py --build-dir build
```

The runner checks the existing build and executes, in order:

1. The full upstream C++ unit-test target, including this branch's four added tests.
2. A separately compiled original-versus-patched socket control.
3. Five replication and process-recovery stages.
4. Four slow-replica backpressure and recovery stages.

The runner does not rebuild the full server or unit-test executable. The build command above is a separate step. Results default to a unique subdirectory below `artifacts/reliability/`; use `--output-dir` to select a different destination:

```bash
python3 tools/reliability/run.py --build-dir build \
  --output-dir artifacts/reliability
```

Each run produces `result.json`, `summary.md`, command logs, and nested experiment results. A nonzero exit means the run failed; inspect the actual logs instead of substituting checked-in evidence. The parent runner also checks that all expected experiment stages passed and that both experiments used the recorded server binary.

### Reusing an external build

`--build-dir` can point to an existing build outside this checkout. The [build identity check](../../tools/reliability/build_identity.py) reads `CMAKE_HOME_DIRECTORY`, byte-compares the original compiled source tree with this checkout, and verifies the source inventory for `src`, `tests/cppunit`, `utils`, `cmake`, `CMakeLists.txt`, and `x.py`. It checks that the production and test translation units are in the compilation database, that the binaries are executable and newer than the original build inputs, and records binary hashes. The identity is checked again after the run.

This permits a byte-identical checkout to reuse an existing build while making that choice explicit. These checks are not a fresh or hermetic rebuild, do not establish compiler provenance, and do not independently rebuild third-party dependencies. Rebuild if the source or configuration differs; do not bypass a failed identity check by editing its inputs.

## Focused C++ tests

```bash
# Four added cases plus the existing listening-address case.
python3 x.py test cpp build --gtest_filter='IOUtil.*:IOUtilSocketTest.*'

# The complete C++ suite.
python3 x.py test cpp build

# Recompile the original and patched socket implementation separately.
python3 tools/reliability/reproduce_socket.py \
  --build-dir build --work-dir artifacts/reliability/socket-reproduction
```

The original control must fail the named deadline test with the watchdog diagnostic. That expected failure is recorded explicitly; it is not counted as a passing original implementation. The patched control must pass all five cases. The control retains its temporary compilation directories and writes their locations into its local result for inspection.

The control's fresh, small builds use the production `io_util.cc`, this branch's real GoogleTest cases, and the dependencies already expanded by the main build. This does not constitute a fresh full Kvrocks build.

## Standalone real-node experiments

```bash
python3 tools/reliability/run_replication_lab.py \
  --binary build/kvrocks --work-dir artifacts/reliability/replication \
  --keys 100 --timeout 60

python3 tools/reliability/run_backpressure_lab.py \
  --binary build/kvrocks --work-dir artifacts/reliability/backpressure \
  --payload-mib 32 --send-timeout-ms 1000 --timeout 60 --total-timeout 180
```

These scripts launch their own master and replica on dynamically allocated loopback ports. They create isolated data directories and signal only their own child processes. `SIGKILL` tests process recovery; `SIGSTOP`/`SIGCONT` tests a stalled reader. The backpressure workload accepts 16-64 MiB and uses 32 MiB by default. See [experiment design](design.md) for each stage's assertions and cleanup.

Use a disposable local Linux environment with enough disk and memory for two Kvrocks processes, the build, and retained test artifacts. Generated outputs are ignored by Git. Before sharing local outputs, remove machine-specific paths and identifiers while preserving outcomes and measured values; the checked-in [evidence notes](evidence.md) describe the published subset.
