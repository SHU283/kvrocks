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

"""Inspect an existing build without claiming to have rebuilt its binaries."""

import hashlib
import json
import os
from pathlib import Path

UPSTREAM_SHA = "baa1da67f2ef94ffd62823116537ab7e95e7d5ba"
SOURCE_DIRECTORIES = ("src", "tests/cppunit", "utils", "cmake")
SOURCE_FILES = ("CMakeLists.txt", "x.py")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_files(root):
    files = {}
    for directory in SOURCE_DIRECTORIES:
        base = root / directory
        if not base.is_dir():
            raise RuntimeError(f"Missing source directory: {base}")
        for path in base.rglob("*"):
            relative = path.relative_to(root)
            if any(part in (".git", "docs", "__pycache__", "build") for part in relative.parts):
                continue
            if path.is_file() and not path.name.upper().startswith("README"):
                files[relative.as_posix()] = path
    for name in SOURCE_FILES:
        path = root / name
        if not path.is_file():
            raise RuntimeError(f"Missing build input: {path}")
        files[name] = path
    return files


def read_cache(path):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith(("#", "//")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.split(":", 1)[0]] = value
    return values


def validate_build(project, build_dir):
    project, build_dir = project.resolve(), build_dir.resolve()
    cache_path = build_dir / "CMakeCache.txt"
    commands_path = build_dir / "compile_commands.json"
    if not cache_path.is_file() or not commands_path.is_file():
        raise RuntimeError("Build first with unit tests and CMAKE_EXPORT_COMPILE_COMMANDS=ON")
    cache = read_cache(cache_path)
    compiled_source = Path(cache.get("CMAKE_HOME_DIRECTORY", "")).resolve()
    if not cache.get("CMAKE_HOME_DIRECTORY") or not compiled_source.is_dir():
        raise RuntimeError("CMAKE_HOME_DIRECTORY does not identify an accessible source tree")
    reference, actual = source_files(project), source_files(compiled_source)
    if reference.keys() != actual.keys():
        difference = sorted(reference.keys() ^ actual.keys())
        raise RuntimeError(f"Build source file inventory differs from this checkout: {difference[:10]}")
    hashes = {}
    for name in sorted(reference):
        expected, observed = sha256(reference[name]), sha256(actual[name])
        if expected != observed:
            raise RuntimeError(f"Build source differs from this checkout: {name}; rebuild before testing")
        hashes[name] = expected
    commands = json.loads(commands_path.read_text(encoding="utf-8"))
    command_files = set()
    for entry in commands:
        path = Path(entry["file"])
        if not path.is_absolute():
            path = Path(entry["directory"]) / path
        command_files.add(path.resolve())
    required = ("src/common/io_util.cc", "tests/cppunit/io_util_test.cc")
    if any((compiled_source / name).resolve() not in command_files for name in required):
        raise RuntimeError("compile_commands.json is missing the production or regression test translation unit")
    newest_source = max(path.stat().st_mtime_ns for path in actual.values())
    newest_build_input = max(newest_source, cache_path.stat().st_mtime_ns, commands_path.stat().st_mtime_ns)
    binaries = {}
    for name in ("kvrocks", "unittest"):
        binary = build_dir / name
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise RuntimeError(f"Missing executable: {binary}")
        if binary.stat().st_mtime_ns < newest_build_input:
            raise RuntimeError(f"Build input is newer than {name}; rebuild before testing")
        binaries[name] = {"sha256": sha256(binary), "mtime_ns": binary.stat().st_mtime_ns}
    return {
        "upstream_commit": UPSTREAM_SHA,
        "build_directory": str(build_dir),
        "compiled_source_directory": str(compiled_source),
        "external_build_reused": compiled_source != project,
        "new_build_performed": False,
        "source_scope": list(SOURCE_DIRECTORIES) + list(SOURCE_FILES),
        "source_files_matched": len(hashes),
        "matched_source_sha256": hashes,
        "compiled_source_newest_mtime_ns": newest_source,
        "cmake_cache_sha256": sha256(cache_path),
        "compile_commands_sha256": sha256(commands_path),
        "required_compile_units": list(required),
        "cmake_build_type": cache.get("CMAKE_BUILD_TYPE"),
        "binaries": binaries,
        "provenance_limit": (
            "Byte matching, compile-command membership, mtimes and binary hashes are consistency checks, "
            "not proof of a fresh or hermetic build. Mtimes are checked in the original compiled source "
            "tree; a byte-identical copied checkout may be newer. Dependencies are not independently rebuilt."
        ),
    }
