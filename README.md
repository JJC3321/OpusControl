# Opus Control

Real-time, AI-native system monitor: a "Mission Control" dashboard for OS orchestration. A C++ agent collects telemetry and pushes to Redis; a Python/FastAPI backend consumes the stream, runs anomaly detection, and serves a React dashboard over WebSocket.

## Architecture

- **System Agent (C++):** Daemon that reads process stats (or mocks them on Windows) and pushes JSON to Redis stream `system:metrics`. Subscribes to `system:commands` for kill/throttle commands.
- **Redis:** Stream `system:metrics` for telemetry; Pub/Sub channel `system:commands` for control.
- **Intelligence Engine (Python):** FastAPI app with WebSocket at `/ws`. Background worker reads the stream, buffers metrics, and runs anomaly detection (Claude API when `ANTHROPIC_API_KEY` is set, else rule-based); when a CPU or memory anomaly is found, a throttle command is published automatically to `system:commands`; anomalies are pushed to connected clients.
- **Frontend (React/Vite + Tailwind):** Dark HUD dashboard: live system load graph, process list, and Flash Alert modal with "Apply Fix" (e.g. throttle slider) when an anomaly is detected.

## Prerequisites

- Docker (for Redis)
- Python 3.10+
- Node 18+ (for client)
- C++17 compiler and hiredis (for agent; on Windows use mock data only)

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

Server runs at `http://localhost:8000`; WebSocket at `ws://localhost:8000/ws`.

### 3. Frontend

```bash
cd client
npm install
npm run dev
```

Open `http://localhost:5173`.

### 4. C++ Agent (optional)

On Linux (or WSL) with hiredis installed:

```bash
cd agent
mkdir build && cd build
cmake ..
make
./monitor
```

Set `REDIS_HOST` and `REDIS_PORT` if Redis is not on localhost:6379. On Windows, the agent can be built with the same CMake if hiredis is available (e.g. vcpkg); it uses **mocked** process data. For real metrics (e.g. `/proc` parsing), build and run the agent on Linux.

## Testing the monitoring

**With the C++ agent**

1. Start Redis, then the backend, then the frontend (see Quick start).
2. Run the monitor: from `agent/build`, run `./monitor` (Linux/WSL) or the built `monitor` on Windows.
3. Open http://localhost:5173. You should see:
   - **Connected** in the header.
   - The **live graph** and **process list** updating every few seconds (mock data).
   - After about 10 seconds, the stub analyzer may detect an anomaly (CPU > 90% or memory > 1500 MB in the mock data); a **Flash Alert** modal appears. Use the throttle slider and **Apply fix** to send a command; the agent prints it in its terminal.

**Without the C++ agent (inject test data)**

1. Start Redis, backend, and frontend. Open http://localhost:5173.
2. From the project root, run:
   ```bash
   cd server && python scripts/inject_test_metrics.py
   ```
3. Within about 10 seconds the backend’s analysis loop will see the injected high-CPU/high-memory entries and push an **anomaly** to the dashboard; the Flash Alert modal should appear. Click **Apply fix** to publish a command to `system:commands` (the C++ agent would react if it were running).

**Verify commands (optional)**

With the C++ agent running, click **Apply fix** in the Flash Alert; the agent terminal should print something like `[CMD] throttle PID 9999 to 0.50 (stub)`.

## Environment

Copy `.env.example` to `.env` and set:

- `REDIS_URL` – default `redis://localhost:6379`
- `ANTHROPIC_API_KEY` – optional; when set, enables Claude-based anomaly detection and **automatic** throttle commands (throttle is published to Redis when Claude identifies high CPU or memory). If unset, the backend uses rule-based detection and still supports manual "Apply fix" from the dashboard.

## Project layout

```
agent/           C++ daemon (monitor.cpp, CMakeLists.txt)
server/          FastAPI + WebSocket + Redis consumer (server.py, requirements.txt)
client/          React/Vite/Tailwind dashboard (src/App.tsx, components, hooks)
docker-compose.yml
.env.example
README.md
```

## License

MIT.
