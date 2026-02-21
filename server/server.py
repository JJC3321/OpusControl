import asyncio
import json
import os
import re
import time
from collections import deque
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

STREAM_KEY = "system:metrics"
COMMANDS_CHANNEL = "system:commands"
BUFFER_MAX_ITEMS = 500
ANALYZE_INTERVAL_SEC = 10
DEFAULT_THROTTLE_VALUE = 0.5
AUTO_FIX_COOLDOWN_SEC = 60

app = FastAPI(title="Opus Control API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

metrics_buffer: deque[dict[str, Any]] = deque(maxlen=BUFFER_MAX_ITEMS)
ws_connections: list[WebSocket] = []
last_auto_fix: dict[int, float] = {}


def get_redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379")


def _rule_based_anomaly(metrics: list[dict]) -> dict[str, Any] | None:
    """Fallback when no Anthropic API key: simple CPU/memory thresholds."""
    if not metrics:
        return None
    for m in metrics:
        cpu = m.get("cpu_percent", 0)
        mem = m.get("mem_mb", 0)
        if cpu > 90:
            return {
                "reasoning_trace": f"Process {m.get('name', m.get('pid'))} (PID {m.get('pid')}) is using {cpu:.1f}% CPU.",
                "suggested_action": "Throttle CPU",
                "target_pid": m.get("pid"),
                "target_name": m.get("name", ""),
            }
        if mem > 1500:
            return {
                "reasoning_trace": f"Process {m.get('name', m.get('pid'))} (PID {m.get('pid')}) using {mem:.0f} MB may indicate memory pressure.",
                "suggested_action": "Throttle CPU",
                "target_pid": m.get("pid"),
                "target_name": m.get("name", ""),
            }
    return None


def _parse_claude_json(text: str) -> dict[str, Any] | None:
    """Extract a single JSON object from Claude response (allow markdown code fence)."""
    text = text.strip()
    if "```" in text:
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    if start == -1:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError:
        return None


async def analyze_with_claude(
    metrics: list[dict],
    redis_client: aioredis.Redis | None,
) -> dict[str, Any] | None:
    """Run anomaly detection via Claude API or rule-based fallback; auto-publish throttle if applicable."""
    if not metrics:
        return None

    api_key = os.getenv("ANTHROPIC_API_KEY")
    result: dict[str, Any] | None = None

    if api_key:
        try:
            from anthropic import AsyncAnthropic

            top = sorted(metrics, key=lambda m: m.get("cpu_percent", 0), reverse=True)[:20]
            metrics_json = json.dumps(top, indent=0)

            prompt = f"""You are an anomaly detector for system metrics. Given the following list of processes (pid, name, cpu_percent, mem_mb), identify at most one critical issue: high CPU (e.g. > 90%) or very high memory (e.g. > 1500 MB).

Respond with exactly one JSON object, no other text. Use this format if there is an anomaly:
{{"anomaly": true, "reasoning_trace": "brief explanation", "suggested_action": "Throttle CPU", "target_pid": <pid number>, "target_name": "<process name>"}}

If there is no critical issue, respond with: {{"anomaly": false}}

Metrics (JSON):
{metrics_json}
"""

            client = AsyncAnthropic(api_key=api_key)
            message = await client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            content = message.content
            if content and len(content) > 0:
                block = content[0]
                text = getattr(block, "text", None) or (block.get("text") if isinstance(block, dict) else None) or str(content)
            else:
                text = str(content)
            parsed = _parse_claude_json(text)
            if parsed and parsed.get("anomaly") and parsed.get("target_pid") is not None:
                result = {
                    "reasoning_trace": parsed.get("reasoning_trace", "Anomaly detected."),
                    "suggested_action": parsed.get("suggested_action", "Throttle CPU"),
                    "target_pid": int(parsed["target_pid"]),
                    "target_name": parsed.get("target_name", ""),
                }
        except Exception:
            result = _rule_based_anomaly(metrics)
    else:
        result = _rule_based_anomaly(metrics)

    if result is None:
        return None

    auto_fix_applied = False
    if (
        result.get("suggested_action") == "Throttle CPU"
        and result.get("target_pid") is not None
        and redis_client is not None
    ):
        pid = result["target_pid"]
        now = time.monotonic()
        if now - last_auto_fix.get(pid, 0) >= AUTO_FIX_COOLDOWN_SEC:
            command = f"throttle:{pid}:{DEFAULT_THROTTLE_VALUE}"
            await redis_client.publish(COMMANDS_CHANNEL, command)
            last_auto_fix[pid] = now
            auto_fix_applied = True

    result["auto_fix_applied"] = auto_fix_applied
    return result


async def stream_consumer(redis_client: aioredis.Redis) -> None:
    last_id = "0"
    while True:
        try:
            results = await redis_client.xread({STREAM_KEY: last_id}, count=50, block=2000)
            if not results:
                continue
            for stream_name, entries in results:
                for entry_id, fields in entries:
                    last_id = entry_id
                    if isinstance(fields, dict) and b"data" in fields:
                        raw = fields[b"data"]
                    elif isinstance(fields, list):
                        data_idx = fields.index(b"data") + 1 if b"data" in fields else -1
                        raw = fields[data_idx] if data_idx >= 0 else None
                    else:
                        continue
                    if raw is None:
                        continue
                    try:
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8")
                        obj = json.loads(raw)
                        metrics_buffer.append(obj)
                    except (json.JSONDecodeError, TypeError):
                        pass
        except asyncio.CancelledError:
            break
        except Exception as e:
            await asyncio.sleep(1)


async def analysis_loop(redis_client: aioredis.Redis) -> None:
    while True:
        await asyncio.sleep(ANALYZE_INTERVAL_SEC)
        snapshot = list(metrics_buffer)
        result = await analyze_with_claude(snapshot, redis_client)
        if result and ws_connections:
            msg = json.dumps({"type": "anomaly", "payload": result})
            for ws in list(ws_connections):
                try:
                    await ws.send_text(msg)
                except Exception:
                    pass


async def metrics_broadcast_loop() -> None:
    """Push latest metrics to WebSocket clients every 1.5s for live graph."""
    while True:
        await asyncio.sleep(1.5)
        await broadcast_metrics()


@app.on_event("startup")
async def startup() -> None:
    redis_client = aioredis.from_url(get_redis_url(), decode_responses=False)
    app.state.redis = redis_client
    asyncio.create_task(stream_consumer(redis_client))
    asyncio.create_task(analysis_loop(redis_client))
    asyncio.create_task(metrics_broadcast_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    if hasattr(app.state, "redis"):
        await app.state.redis.aclose()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    ws_connections.append(websocket)
    try:
        await websocket.send_text(
            json.dumps({"type": "connected", "message": "Mission Control connected"})
        )
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
                if data.get("type") == "apply_fix" and "command" in data:
                    redis_client = app.state.redis
                    await redis_client.publish(COMMANDS_CHANNEL, data["command"])
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in ws_connections:
            ws_connections.remove(websocket)


@app.get("/metrics/snapshot")
async def metrics_snapshot() -> dict[str, Any]:
    return {"metrics": list(metrics_buffer)}


async def broadcast_metrics() -> None:
    if not ws_connections:
        return
    snapshot = list(metrics_buffer)
    msg = json.dumps({"type": "metrics", "payload": snapshot})
    for ws in list(ws_connections):
        try:
            await ws.send_text(msg)
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
