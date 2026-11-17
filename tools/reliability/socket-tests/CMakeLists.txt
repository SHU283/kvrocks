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

cmake_minimum_required(VERSION 3.16)
project(kvrocks_socket_regression LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
get_filename_component(DEFAULT_SOURCE "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)
set(KVROCKS_SOURCE "${DEFAULT_SOURCE}" CACHE PATH "Kvrocks checkout")
set(KVROCKS_DEPS "${KVROCKS_SOURCE}/build/_deps" CACHE PATH "Expanded dependencies from a configured Kvrocks build")
set(SOCKET_IO_SOURCE "${KVROCKS_SOURCE}/src/common/io_util.cc" CACHE FILEPATH "Socket implementation under test")
find_package(Threads REQUIRED)

# Reuse the production implementation and tests without linking the complete database.
add_executable(socket_regression
  "${SOCKET_IO_SOURCE}"
  "${KVROCKS_SOURCE}/tests/cppunit/io_util_test.cc"
  "${KVROCKS_DEPS}/gtest-src/googletest/src/gtest-all.cc"
  "${KVROCKS_DEPS}/gtest-src/googletest/src/gtest_main.cc")
target_include_directories(socket_regression PRIVATE
  "${KVROCKS_SOURCE}/src"
  "${KVROCKS_SOURCE}/src/common"
  "${KVROCKS_DEPS}/gtest-src/googletest/include"
  "${KVROCKS_DEPS}/gtest-src/googletest"
  "${KVROCKS_DEPS}/fmt-src/include"
  "${KVROCKS_DEPS}/spdlog-src/include"
  "${KVROCKS_DEPS}/rocksdb-src/include"
  "${KVROCKS_DEPS}/libevent-src/include"
  "${KVROCKS_DEPS}/libevent-build/include")
target_compile_definitions(socket_regression PRIVATE FMT_HEADER_ONLY SPDLOG_FMT_EXTERNAL)
target_compile_options(socket_regression PRIVATE -ffunction-sections -fdata-sections)
target_link_options(socket_regression PRIVATE -Wl,--gc-sections)
target_link_libraries(socket_regression PRIVATE Threads::Threads)
