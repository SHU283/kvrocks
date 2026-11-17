# Kvrocks Socket Reliability and Replication Recovery

A focused C++/Linux reliability project built on Apache Kvrocks. This branch fixes a blocking socket send path, adds real-socket regression tests, and provides repeatable replication fault experiments. Apache Kvrocks supplies the database, storage integration, protocol handling, and replication system; this is not a new database implementation.

The analysis and patch are based on upstream commit [`baa1da67f2ef94ffd62823116537ab7e95e7d5ba`](https://github.com/apache/kvrocks/commit/baa1da67f2ef94ffd62823116537ab7e95e7d5ba). No upstream pull request has been submitted for this patch.

## What I Fixed

`SockSendWithTimeout` waited for writability with `poll`, then called blocking `write` on a replication socket. A writable notification does not guarantee that a large blocking write will finish before the deadline. A peer that stops reading can keep the thread inside `write`, bypassing the outer timeout check.

The patch also retries `poll` and `send` when interrupted by a signal (`EINTR`). See the [bug analysis and exact production diff](docs/reliability/socket-timeout.md).

## How I Fixed It

- Use `send(..., MSG_DONTWAIT)` for each send in the positive-timeout, non-TLS path.
- Retry interrupted waits and sends without resetting the original time budget.
- Preserve the file descriptor's existing `O_NONBLOCK` setting.

The `steady_clock` deadline, partial-write offset tracking, and `EAGAIN` handling already existed upstream. This patch preserves those mechanisms and closes the blocking-call and interruption gaps. [Design decisions](docs/reliability/design.md) explain the scope and tradeoffs.

## How I Tested It

- **Four new GoogleTest cases:** blocked sender timeout, binary data and descriptor flags, repeated signal interruption, and partial writes to a slow reader.
- **Before/after control:** the original implementation must fail with the specific watchdog diagnostic; the patched implementation must pass the socket tests.
- **Five replication stages:** initial writes, initial replication, replica crash and catch-up, master crash and persistence, and replication after master restart.
- **Four backpressure stages:** initial synchronization, `SIGSTOP` plus a bounded 32 MiB write workload and observed send timeout, `SIGCONT` plus data verification, and continued replication.

The Linux/WSL revalidation on **2026-11-17** recorded **680 passed, 1 skipped, 0 failed** across 681 C++ tests in 87 suites. The original implementation failed the designated timeout regression as expected; the patched control passed all five focused cases. All nine real-node experiment stages passed. The full suite includes upstream tests; only four socket cases were added here.

This revalidation reused the existing server and unit-test build; it was **not a fresh full build**. The control separately compiled the original and patched socket implementation. See the [validation notes](docs/reliability/evidence.md) and [sanitized validation record](examples/reliability/validation.json) for the run identity, skipped test, and scope. These observations describe one recorded environment.

The recorded real-replication timeout reported `sent 0`. It demonstrates timeout handling and recovery, but does not independently prove that the original implementation would block inside `write` in that same experiment. The direct old-versus-patched regression is provided by the local socket control test.

## How to Reproduce

Use Linux or WSL2. The development baseline is Ubuntu 24.04, a C++20 compiler, CMake, Ninja, and Python 3.11 or newer. The Python experiments use only the standard library.

```bash
sudo apt-get install build-essential cmake ninja-build python3 git autoconf automake libtool pkg-config
python3 x.py build --ninja --unittest -j4

# Complete C++ suite, before/after control, and both replication experiments.
python3 tools/reliability/run.py --build-dir build

# Run only the focused socket tests.
python3 x.py test cpp build --gtest_filter='IOUtil.*:IOUtilSocketTest.*'
```

The build downloads the dependencies pinned by upstream. Experiment nodes bind to loopback, use isolated data directories, and are cleaned up after each run. The runner verifies and uses an existing build, then writes local results beneath `artifacts/reliability/`; generated binaries, caches, and database files are excluded from version control. See [reproduction commands](docs/reliability/reproduction.md) and [experiment design](docs/reliability/design.md).

## My Contributions

| Area | Review entry point | Scope |
|---|---|---|
| Production C++ | [`src/common/io_util.cc`](src/common/io_util.cc), `SockSendWithTimeout` | Per-call nonblocking send and `EINTR` retry |
| C++ regression tests | [`tests/cppunit/io_util_test.cc`](tests/cppunit/io_util_test.cc) | Four real-socket cases with bounded cleanup |
| Fault experiments | [`tools/reliability/run_replication_lab.py`](tools/reliability/run_replication_lab.py), [`run_backpressure_lab.py`](tools/reliability/run_backpressure_lab.py) | Data, deletion, offset, timeout-log, and recovery checks |
| Reproduction and evidence | [`tools/reliability/run.py`](tools/reliability/run.py), [`reproduce_socket.py`](tools/reliability/reproduce_socket.py) | Executable checks, before/after control, and retained results |

The [contribution map](docs/reliability/contributions.md) distinguishes upstream mechanisms from this branch's changes and provides review commands. Validation is limited to Linux non-TLS socket behavior, asynchronous replication, and process failures. It does not establish power-loss durability, automatic failover, linearizability, or throughput improvements. Go integration tests, TLS, and other operating systems are outside the recorded validation scope.

---

The original Apache Kvrocks README follows below. Upstream copyright, `LICENSE`, and `NOTICE` are retained.

<!--
 Licensed to the Apache Software Foundation (ASF) under one
 or more contributor license agreements.  See the NOTICE file
 distributed with this work for additional information
 regarding copyright ownership.  The ASF licenses this file
 to you under the Apache License, Version 2.0 (the
 "License"); you may not use this file except in compliance
 with the License.  You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing,
 software distributed under the License is distributed on an
 "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 KIND, either express or implied.  See the License for the
 specific language governing permissions and limitations
 under the License.
-->

<img src="https://kvrocks.apache.org/img/kvrocks-featured.png" alt="kvrocks_logo" width="350"/>

[![CI](https://github.com/apache/kvrocks/actions/workflows/kvrocks.yaml/badge.svg?branch=unstable)](https://github.com/apache/kvrocks/actions/workflows/kvrocks.yaml)
[![License](https://img.shields.io/github/license/apache/kvrocks)](https://github.com/apache/kvrocks/blob/unstable/LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/apache/kvrocks)](https://github.com/apache/kvrocks/stargazers)

---
* [Chat on Zulip](https://kvrocks.zulipchat.com/)
* [Mailing List](https://lists.apache.org/list.html?dev@kvrocks.apache.org) ([how to subscribe](https://www.apache.org/foundation/mailinglists.html#subscribing))

**Apache Kvrocks** is a distributed key value NoSQL database that uses RocksDB as storage engine and is compatible with Redis protocol. Kvrocks intends to decrease the cost of memory and increase the capacity while compared to Redis. The design of replication and storage was inspired by [rocksplicator](https://github.com/pinterest/rocksplicator) and [blackwidow](https://github.com/Qihoo360/blackwidow).

Kvrocks has the following key features:

* Redis Compatible: Users can access Apache Kvrocks via any Redis client.
* Namespace: Similar to Redis SELECT but equipped with token per namespace.
* Replication: Async replication using binlog like MySQL.
* High Availability: Support Redis sentinel to failover when master or slave was failed.
* Cluster: Centralized management but accessible via any Redis cluster client.

## Who uses Kvrocks

You can find Kvrocks users at [the Users page](https://kvrocks.apache.org/users/).

Users are encouraged to add themselves to the Users page. Either leave a comment on the ["Who is using Kvrocks"](https://github.com/apache/kvrocks/issues/414) issue, or directly send a pull request to add company or organization [information](https://github.com/apache/kvrocks-website/blob/main/src/components/UserLogos/index.tsx) and [logo](https://github.com/apache/kvrocks-website/tree/main/static/media/users).

## Build and run Kvrocks

### Prerequisite

```shell
# Ubuntu / Debian
sudo apt update
sudo apt install -y git build-essential cmake libtool python3 libssl-dev

# CentOS / RedHat
sudo yum install -y centos-release-scl-rh
sudo yum install -y git devtoolset-11 autoconf automake libtool libstdc++-static python3 openssl-devel
# download and install cmake via https://cmake.org/download
wget https://github.com/Kitware/CMake/releases/download/v3.26.4/cmake-3.26.4-linux-x86_64.sh -O cmake.sh
sudo bash cmake.sh --skip-license --prefix=/usr
# enable gcc and make in devtoolset-11
source /opt/rh/devtoolset-11/enable

# openSUSE / SUSE Linux Enterprise
sudo zypper install -y gcc11 gcc11-c++ make wget git autoconf automake python3 curl cmake

# Arch Linux
sudo pacman -Sy --noconfirm autoconf automake python3 git wget which cmake make gcc

# macOS
brew install git cmake autoconf automake libtool openssl
# please link openssl by force if it still cannot be found after installing
brew link --force openssl
```

### Build

It is as simple as:

```shell
$ git clone https://github.com/apache/kvrocks.git
$ cd kvrocks
$ ./x.py build # `./x.py build -h` to check more options
```

To build with TLS support, you'll need OpenSSL development libraries (e.g. libssl-dev on Debian/Ubuntu) and run:

```shell
$ ./x.py build -DENABLE_OPENSSL=ON
```

To build with lua instead of luaJIT, run:

```shell
$ ./x.py build -DENABLE_LUAJIT=OFF
```

Build with debug mode, run:

```shell
# The default build type is RelWithDebInfo and its optimization level is typically -O2.
# You can change it to -O0 in debug mode.

$ ./x.py build -DCMAKE_BUILD_TYPE=Debug
```

### Running Kvrocks

```shell
$ ./build/kvrocks -c kvrocks.conf
```

### Running Kvrocks using Docker

```shell
$ docker run -it -p 6666:6666 apache/kvrocks --bind 0.0.0.0
# or get the nightly image:
$ docker run -it -p 6666:6666 apache/kvrocks:nightly
```

Please visit [Apache Kvrocks on DockerHub](https://hub.docker.com/r/apache/kvrocks) for additional details about images.

### Connect Kvrocks service

```sh
$ redis-cli -p 6666

127.0.0.1:6666> get a
(nil)
```

### Running test cases

```shell
$ ./x.py build --unittest
$ ./x.py test cpp # run C++ unit tests
$ ./x.py test go # run Golang (unit and integration) test cases
```

### Supported platforms

* OS: Linux and macOS
* arch: x86_64, ARM and RISC-V

## Namespace

Namespace is used to isolate data between users. Unlike all the Redis databases can be visited by `requirepass`, we use one token per namespace. `requirepass` is regarded as admin token, and only admin token allows to access the namespace command, as well as some commands like `config`, `slaveof`, `bgsave`, etc. See the [Namespace](https://kvrocks.apache.org/docs/namespace) page for more details.

```sh
# add token
127.0.0.1:6666> namespace add ns1 my_token
OK

# update token
127.0.0.1:6666> namespace set ns1 new_token
OK

# list namespace
127.0.0.1:6666> namespace get *
1) "ns1"
2) "new_token"
3) "__namespace"
4) "foobared"

# delete namespace
127.0.0.1:6666> namespace del ns1
OK
```

## Cluster

Kvrocks implements a proxyless centralized cluster solution but its accessing method is completely compatible with Redis cluster clients. You can use Redis cluster SDKs to access the kvrocks cluster. For more details, please refer to [Kvrocks Cluster Introduction](https://kvrocks.apache.org/docs/cluster/).

## Documents

Documents are hosted at the [official website](https://kvrocks.apache.org/docs/getting-started/).

* [Supported Commands](https://kvrocks.apache.org/docs/supported-commands/)
* [Design Complex Structure on RocksDB](https://kvrocks.apache.org/community/data-structure-on-rocksdb/)
* [Replication Design](https://kvrocks.apache.org/docs/replication)

## Tools

* To manage Kvrocks clusters for failover, scaling up/down and more, use [kvrocks-controller](https://github.com/apache/kvrocks-controller)
* To export the Kvrocks monitor metrics, use [kvrocks_exporter](https://github.com/RocksLabs/kvrocks_exporter)
* To migrate from Redis to Kvrocks, use [RedisShake](https://github.com/tair-opensource/RedisShake)
* To migrate from Kvrocks to Redis, use `kvrocks2redis` built via `./x.py build`

## Contributing

Kvrocks community welcomes all forms of contribution and you can find out how to get involved on the [Community](https://kvrocks.apache.org/community/) and [How to Contribute](https://kvrocks.apache.org/community/contributing) pages.

## License

Apache Kvrocks is licensed under the Apache License Version 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE) for details.

## Social Media

- [Medium](https://kvrocks.medium.com/)
- [X (Twitter)](https://twitter.com/apache_kvrocks)
- [Zhihu](https://www.zhihu.com/people/kvrocks) (in Chinese)
- WeChat Official Account (in Chinese, scan the QR code to follow)

![WeChat official account](assets/wechat_account.jpg)
