/*
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 *
 */
#include "common/io_util.h"

#include <fcntl.h>
#include <gtest/gtest.h>
#include <pthread.h>
#include <sys/socket.h>

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <condition_variable>
#include <csignal>
#include <mutex>
#include <string>
#include <thread>

#include "common/scope_exit.h"
#include "common/unique_fd.h"

namespace {

volatile std::sig_atomic_t signal_received = 0;

void RecordSignal(int) { signal_received = 1; }

class IOUtilSocketTest : public testing::Test {
 protected:
  void SetUp() override {
    int fds[2];
    ASSERT_EQ(socketpair(AF_UNIX, SOCK_STREAM, 0, fds), 0);
    sender_.Reset(fds[0]);
    receiver_.Reset(fds[1]);
  }

  UniqueFD sender_;
  UniqueFD receiver_;
};

}  // namespace

TEST(IOUtil, MatchListeningIP) {
  // bind 0.0.0.0 should at least match 127.0.0.1
  std::vector<std::string> binds{"0.0.0.0"};
  ASSERT_TRUE(util::MatchListeningIP(binds, "127.0.0.1"));
}

TEST_F(IOUtilSocketTest, SendWithTimeoutHonorsDeadlineOnBlockingSocket) {
  int send_buffer_size = 4096;
  ASSERT_EQ(setsockopt(*sender_, SOL_SOCKET, SO_SNDBUF, &send_buffer_size, sizeof(send_buffer_size)), 0);
  const int original_flags = fcntl(*sender_, F_GETFL);
  ASSERT_NE(original_flags, -1);
  ASSERT_EQ(original_flags & O_NONBLOCK, 0);

  const std::string payload(1024 * 1024, 'x');
  std::mutex mutex;
  std::condition_variable completed;
  bool finished = false;
  Status result;
  std::chrono::steady_clock::duration elapsed{};

  std::thread sender([&] {
    // A watchdog shutdown must not terminate the test process through SIGPIPE.
    sigset_t mask;
    sigemptyset(&mask);
    sigaddset(&mask, SIGPIPE);
    pthread_sigmask(SIG_BLOCK, &mask, nullptr);

    const auto start = std::chrono::steady_clock::now();
    result = util::SockSendWithTimeout(*sender_, payload, 100);
    elapsed = std::chrono::steady_clock::now() - start;
    {
      std::lock_guard<std::mutex> lock(mutex);
      finished = true;
    }
    completed.notify_one();
  });

  bool watchdog_fired = false;
  {
    std::unique_lock<std::mutex> lock(mutex);
    if (!completed.wait_for(lock, std::chrono::seconds(2), [&] { return finished; })) {
      watchdog_fired = true;
      shutdown(*receiver_, SHUT_RDWR);
    }
  }
  sender.join();

  EXPECT_FALSE(watchdog_fired) << "Socket send ignored its deadline and needed watchdog cleanup";
  EXPECT_FALSE(result.IsOK());
  EXPECT_NE(result.Msg().find("timeout"), std::string::npos) << result.Msg();
  EXPECT_LT(elapsed, std::chrono::seconds(1));
  EXPECT_EQ(fcntl(*sender_, F_GETFL), original_flags);
}

TEST_F(IOUtilSocketTest, SendWithTimeoutSendsDataAndPreservesSocketFlags) {
  timeval receive_timeout{1, 0};
  ASSERT_EQ(setsockopt(*receiver_, SOL_SOCKET, SO_RCVTIMEO, &receive_timeout, sizeof(receive_timeout)), 0);
  std::string payload(4096, 'x');
  payload[42] = '\0';

  for (bool nonblocking : {false, true}) {
    SCOPED_TRACE(nonblocking);
    ASSERT_TRUE(util::SockSetBlocking(*sender_, nonblocking ? 0 : 1).IsOK());
    const int original_flags = fcntl(*sender_, F_GETFL);
    ASSERT_NE(original_flags, -1);

    const auto result = util::SockSendWithTimeout(*sender_, payload, 1000);
    ASSERT_TRUE(result.IsOK()) << result.Msg();
    EXPECT_EQ(fcntl(*sender_, F_GETFL), original_flags);

    std::string received(payload.size(), '\0');
    ASSERT_EQ(recv(*receiver_, received.data(), received.size(), MSG_WAITALL), static_cast<ssize_t>(received.size()));
    EXPECT_EQ(received, payload);
  }
}

TEST_F(IOUtilSocketTest, SendWithTimeoutKeepsDeadlineAfterInterruptedPoll) {
  int send_buffer_size = 4096;
  ASSERT_EQ(setsockopt(*sender_, SOL_SOCKET, SO_SNDBUF, &send_buffer_size, sizeof(send_buffer_size)), 0);
  const std::string padding(4096, 'x');
  while (send(*sender_, padding.data(), padding.size(), MSG_DONTWAIT) > 0) {
  }
  ASSERT_TRUE(errno == EAGAIN || errno == EWOULDBLOCK);

  struct sigaction action = {}, previous_action = {};
  action.sa_handler = RecordSignal;
  sigemptyset(&action.sa_mask);
  ASSERT_EQ(sigaction(SIGUSR1, &action, &previous_action), 0);
  auto restore_handler = MakeScopeExit([&] { EXPECT_EQ(sigaction(SIGUSR1, &previous_action, nullptr), 0); });
  signal_received = 0;

  std::mutex mutex;
  std::condition_variable completed;
  bool finished = false;
  int mask_result = 0;
  Status result;
  std::chrono::steady_clock::duration elapsed{};
  std::thread sender([&] {
    sigset_t mask;
    sigemptyset(&mask);
    sigaddset(&mask, SIGUSR1);
    mask_result = pthread_sigmask(SIG_UNBLOCK, &mask, nullptr);
    const auto start = std::chrono::steady_clock::now();
    result = util::SockSendWithTimeout(*sender_, "deadline", 200);
    elapsed = std::chrono::steady_clock::now() - start;
    {
      std::lock_guard<std::mutex> lock(mutex);
      finished = true;
    }
    completed.notify_one();
  });

  // Keep interrupting longer than the deadline to detect accidental timeout resets.
  const auto stop_signals = std::chrono::steady_clock::now() + std::chrono::milliseconds(1500);
  while (std::chrono::steady_clock::now() < stop_signals) {
    std::unique_lock<std::mutex> lock(mutex);
    if (completed.wait_for(lock, std::chrono::milliseconds(5), [&] { return finished; })) break;
    // The sender cannot exit while this lock is held, so its pthread handle remains valid.
    EXPECT_EQ(pthread_kill(sender.native_handle(), SIGUSR1), 0);
  }
  sender.join();

  EXPECT_EQ(mask_result, 0);
  EXPECT_EQ(signal_received, 1);
  EXPECT_FALSE(result.IsOK());
  EXPECT_NE(result.Msg().find("timeout"), std::string::npos) << result.Msg();
  EXPECT_GE(elapsed, std::chrono::milliseconds(150));
  EXPECT_LT(elapsed, std::chrono::seconds(1));
}

TEST_F(IOUtilSocketTest, SendWithTimeoutCompletesPartialWritesToSlowReader) {
  int send_buffer_size = 4096;
  ASSERT_EQ(setsockopt(*sender_, SOL_SOCKET, SO_SNDBUF, &send_buffer_size, sizeof(send_buffer_size)), 0);
  ASSERT_TRUE(util::SockSetBlocking(*sender_, 0).IsOK());
  const int original_flags = fcntl(*sender_, F_GETFL);
  ASSERT_NE(original_flags, -1);
  timeval receive_timeout{2, 0};
  ASSERT_EQ(setsockopt(*receiver_, SOL_SOCKET, SO_RCVTIMEO, &receive_timeout, sizeof(receive_timeout)), 0);

  std::string payload(256 * 1024, '\0');
  for (size_t i = 0; i < payload.size(); ++i) payload[i] = static_cast<char>(i % 251);
  socklen_t option_size = sizeof(send_buffer_size);
  ASSERT_EQ(getsockopt(*sender_, SOL_SOCKET, SO_SNDBUF, &send_buffer_size, &option_size), 0);
  ASSERT_GT(payload.size(), static_cast<size_t>(send_buffer_size));

  std::string received(payload.size(), '\0');
  size_t received_bytes = 0;
  int receive_error = 0;
  std::thread reader([&] {
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    while (received_bytes < received.size()) {
      auto n = recv(*receiver_, received.data() + received_bytes,
                    std::min(size_t{2048}, received.size() - received_bytes), 0);
      if (n < 0 && errno == EINTR) continue;
      if (n <= 0) {
        if (n < 0) receive_error = errno;
        break;
      }
      received_bytes += static_cast<size_t>(n);
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
  });

  const auto result = util::SockSendWithTimeout(*sender_, payload, 3000);
  shutdown(*sender_, SHUT_WR);
  reader.join();

  EXPECT_TRUE(result.IsOK()) << result.Msg();
  EXPECT_EQ(receive_error, 0);
  EXPECT_EQ(received_bytes, payload.size());
  EXPECT_EQ(received, payload);
  EXPECT_EQ(fcntl(*sender_, F_GETFL), original_flags);
}
