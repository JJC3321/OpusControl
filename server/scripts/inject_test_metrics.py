"""
Inject test metrics into Redis stream `system:metrics` for testing the dashboard
without a metrics collector. Includes one high-CPU and one high-memory entry so the
stub analyzer will trigger an anomaly (run backend and open dashboard first).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis

STREAM_KEY = "system:metrics"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

NORMAL = [
    {"pid": 1001, "cpu_percent": 12.5, "mem_mb": 80, "name": "systemd"},
    {"pid": 1002, "cpu_percent": 2.1, "mem_mb": 45, "name": "sshd"},
    {"pid": 1003, "cpu_percent": 8.0, "mem_mb": 120, "name": "nginx"},
]
ANOMALY_CPU = {"pid": 9999, "cpu_percent": 95.0, "mem_mb": 200, "name": "runaway_process"}
ANOMALY_MEM = {"pid": 9998, "cpu_percent": 5.0, "mem_mb": 1800, "name": "memory_hog"}


def main() -> None:
    r = redis.from_url(REDIS_URL)
    for obj in NORMAL:
        r.xadd(STREAM_KEY, {"data": json.dumps(obj)})
    r.xadd(STREAM_KEY, {"data": json.dumps(ANOMALY_CPU)})
    r.xadd(STREAM_KEY, {"data": json.dumps(ANOMALY_MEM)})
    print("Injected test metrics (including high CPU and high memory).")
    print("Backend analyzes every 10s; open the dashboard and wait for the Flash Alert.")


if __name__ == "__main__":
    main()
