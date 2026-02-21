# Opus Control

Real-time, AI-native dashboard for monitoring an **AWS EC2 instance** and controlling its resources. A collector on the EC2 instance pushes telemetry to the backend; the **Anthropic (Claude) API** analyzes load and sends allocation changes back to the EC2 instance. You can also drag a line on the graph to allocate or deallocate resources; Claude monitors that line and can adjust it to match demand.

## What it does

- **Monitors an EC2 instance:** The backend receives metrics (CPU, memory, processes) from the instance via Redis. The dashboard shows system load over time and a process list (top by CPU).
- **Claude sends changes to EC2 resources:** Claude analyzes the metrics and your allocation intent (the line on the graph) and suggests resource allocation (throttle/priority). Those changes are published as commands (e.g. throttle a process) so the agent on the EC2 instance can apply them.
- **User control:** Drag the allocation line on the graph up (more resources) or down (fewer). Your choice is sent to the backend; Claude can also update the line based on demand.
- **Context panel:** Configure watch/ignore process lists, CPU and memory thresholds, and time window (GET/PUT `/api/context`).

## Architecture

- **EC2 instance:** Runs an agent that collects process stats and pushes JSON to the Redis stream `system:metrics`. Subscribes to the Redis channel `system:commands` for throttle/kill commands from the backend (e.g. `setpriority()` on Linux).
- **Backend (Python/FastAPI):** Consumes the Redis stream, buffers metrics, and runs anomaly detection (Claude when `ANTHROPIC_API_KEY` is set, else rule-based). Claude receives demand/allocation and can suggest allocation; the backend publishes throttle/kill to Redis so the EC2 agent applies the changes. Broadcasts metrics and allocation over WebSocket. Serves REST: `/api/context`, `/health`, `/metrics/snapshot`.
- **Frontend (React/Vite + Tailwind):** Dark HUD dashboard. **LiveGraph** shows system load and an allocation line; drag the line to set allocation. **ContextPanel** for watch/ignore and thresholds. Process list (top by CPU).
- **Redis:** Stream `system:metrics` for EC2 telemetry; channel `system:commands` for control commands; keys for context and overrides.

## Prerequisites

- Docker (for Redis)
- Python 3.10+
- Node 18+ (for client)
- Claude API Key
- On the EC2 instance: C++17 and hiredis for the agent (or use the test script for development)

## Quick start

### 1. Redis

```bash
docker compose up -d
```

### 2. Backend

```bash
cd server
cp ../.env.example ../.env
pip install -r requirements.txt
python server.py
```

Server: `http://localhost:8000`; WebSocket: `ws://localhost:8000/ws`.

### 3. Frontend

```bash
cd client
npm install
npm run dev
```

Open `http://localhost:5173`. The dev server proxies `/api` to the backend.

### 4. Agent on EC2 (or locally for dev)

On the EC2 instance (or Linux/WSL) with hiredis:

```bash
cd agent
mkdir build && cd build
cmake ..
make
./monitor
```

Set `REDIS_HOST` and `REDIS_PORT` so the agent points at your Redis (e.g. from the machine running the backend). The agent pushes metrics to Redis and receives throttle/kill commands; Claude’s suggestions are sent as those commands so the EC2 resources are adjusted.

For local testing without EC2, you can inject test metrics: `cd server && python scripts/inject_test_metrics.py`.

## Using the dashboard

1. Start Redis, backend, and frontend (steps 1–3 above). Run the agent on the EC2 instance (or inject test data locally).
2. Open http://localhost:5173. You should see **Connected**, the **live graph** (load + allocation line), and the **process list**.
3. **Drag** the allocation line up or down to allocate or deallocate resources. The backend stores it and sends the corresponding commands so the EC2 agent can apply them.
4. With `ANTHROPIC_API_KEY` set, Claude analyzes metrics and allocation and can send allocation updates; the graph line moves when Claude’s suggestion is applied, and the backend publishes the commands to change EC2 resources.

## Environment

In `.env` (copy from `.env.example`):

- **`ANTHROPIC_API_KEY`** – When set, Claude analyzes EC2 metrics and allocation and sends resource changes (throttle/kill) to the instance. Get a key from [Anthropic](https://console.anthropic.com/).
- `REDIS_URL` – default `redis://localhost:6379` (point to Redis that the EC2 agent can reach if deployed)

## Project layout

```
agent/                   Collector daemon (monitor.cpp); runs on EC2
server/
  server.py              FastAPI: metrics, Claude allocation/anomaly, context API
  scripts/inject_test_metrics.py
client/
  src/
    App.tsx
    components/          LiveGraph, ContextPanel
    hooks/useWebSocket.ts
  vite.config.ts         Proxies /api to backend
docker-compose.yml       Redis
.env.example
README.md
```

## License

MIT.
