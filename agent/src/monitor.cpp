#include <hiredis/hiredis.h>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>

namespace {

const char* REDIS_STREAM = "system:metrics";
const char* REDIS_COMMANDS_CHANNEL = "system:commands";
const int METRICS_INTERVAL_SEC = 2;
const int MAX_PROCESSES_MOCK = 8;

std::string get_env(const char* name, const char* default_val) {
  const char* v = std::getenv(name);
  return v ? std::string(v) : std::string(default_val);
}

std::string build_process_json(int pid, double cpu_percent, double mem_mb, const std::string& name) {
  std::ostringstream oss;
  oss << "{\"pid\":" << pid << ",\"cpu_percent\":" << cpu_percent
      << ",\"mem_mb\":" << mem_mb << ",\"name\":\"" << name << "\"}";
  return oss.str();
}

void push_metrics_loop(redisContext* ctx) {
  const char* names[] = {"systemd", "sshd", "nginx", "node", "python", "monitor", "chrome", "code"};
  std::srand(static_cast<unsigned>(std::time(nullptr)));

  while (true) {
    for (int i = 0; i < MAX_PROCESSES_MOCK; ++i) {
      int pid = 1000 + i * 100 + (std::rand() % 50);
      double cpu = (std::rand() % 10000) / 100.0;
      double mem = (std::rand() % 2048) + 10.0;
      std::string name = names[i % (sizeof(names) / sizeof(names[0]))];
      std::string data = build_process_json(pid, cpu, mem, name);

      redisReply* reply = static_cast<redisReply*>(
          redisCommand(ctx, "XADD %s * data %b", REDIS_STREAM, data.c_str(), data.size()));
      if (!reply) {
        std::cerr << "XADD failed (connection lost?)\n";
        freeReplyObject(reply);
        return;
      }
      if (reply->type == REDIS_REPLY_ERROR) {
        std::cerr << "XADD error: " << reply->str << "\n";
      }
      freeReplyObject(reply);
    }
    std::this_thread::sleep_for(std::chrono::seconds(METRICS_INTERVAL_SEC));
  }
}

void handle_command(const std::string& cmd) {
  if (cmd.find("kill:") == 0) {
    std::string pid_str = cmd.substr(5);
    std::cout << "[CMD] kill requested for PID " << pid_str << " (stub)\n";
    return;
  }
  if (cmd.find("throttle:") == 0) {
    size_t first = 8;
    size_t sep = cmd.find(':', first);
    if (sep != std::string::npos) {
      std::string pid_str = cmd.substr(first, sep - first);
      std::string value = cmd.substr(sep + 1);
      std::cout << "[CMD] throttle PID " << pid_str << " to " << value << " (stub)\n";
    }
    return;
  }
  std::cout << "[CMD] unknown: " << cmd << "\n";
}

void subscribe_loop(redisContext* sub_ctx) {
  redisReply* reply = static_cast<redisReply*>(
      redisCommand(sub_ctx, "SUBSCRIBE %s", REDIS_COMMANDS_CHANNEL));
  if (!reply || reply->type == REDIS_REPLY_ERROR) {
    if (reply) {
      std::cerr << "SUBSCRIBE error: " << reply->str << "\n";
      freeReplyObject(reply);
    }
    return;
  }
  freeReplyObject(reply);

  while (true) {
    if (redisGetReply(sub_ctx, reinterpret_cast<void**>(&reply)) != REDIS_OK) {
      std::cerr << "redisGetReply failed\n";
      break;
    }
    if (!reply) break;
    if (reply->type == REDIS_REPLY_ARRAY && reply->elements >= 3) {
      redisReply* msg = reply->element[2];
      if (msg->type == REDIS_REPLY_STRING && msg->len > 0) {
        std::string cmd(msg->str, msg->len);
        handle_command(cmd);
      }
    }
    freeReplyObject(reply);
  }
}

redisContext* connect_redis(const std::string& host, int port) {
  struct timeval timeout = {2, 0};
  redisContext* c = redisConnectWithTimeout(host.c_str(), port, timeout);
  if (!c || c->err) {
    if (c) {
      std::cerr << "Redis connection error: " << c->errstr << "\n";
      redisFree(c);
    }
    return nullptr;
  }
  return c;
}

}  // namespace

int main() {
  std::string host = get_env("REDIS_HOST", "localhost");
  int port = 6379;
  const char* port_env = std::getenv("REDIS_PORT");
  if (port_env) port = std::atoi(port_env);

  redisContext* metrics_ctx = connect_redis(host, port);
  redisContext* sub_ctx = connect_redis(host, port);
  if (!metrics_ctx || !sub_ctx) {
    if (metrics_ctx) redisFree(metrics_ctx);
    if (sub_ctx) redisFree(sub_ctx);
    return 1;
  }

  std::thread subscriber_thread([sub_ctx]() { subscribe_loop(sub_ctx); });
  push_metrics_loop(metrics_ctx);

  redisFree(metrics_ctx);
  redisFree(sub_ctx);
  if (subscriber_thread.joinable()) subscriber_thread.join();
  return 0;
}
