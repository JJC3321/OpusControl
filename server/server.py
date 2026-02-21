import asyncio
import base64
import io
import json
import math
import os
import re
import time
from collections import deque
from typing import Any

import redis.asyncio as aioredis
from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

STREAM_KEY = "system:metrics"
COMMANDS_CHANNEL = "system:commands"
BUFFER_MAX_ITEMS = 500
ANALYZE_INTERVAL_SEC = 10
DEFAULT_THROTTLE_VALUE = 0.5
AUTO_FIX_COOLDOWN_SEC = 60

CONTEXT_KEY_WATCH = "opus:context:watch"
CONTEXT_KEY_IGNORE = "opus:context:ignore"
CONTEXT_KEY_THRESHOLDS = "opus:context:thresholds"
CONTEXT_KEY_TIME_WINDOW = "opus:context:time_window_sec"
OVERRIDES_KEY = "opus:overrides"
DISMISS_SUGGEST_THRESHOLD = 3
DEFAULT_CPU_THRESHOLD = 90
DEFAULT_MEM_THRESHOLD_MB = 1500
METRICS_BROADCAST_INTERVAL_SEC = 1.5
SIMULATOR_COSINE_PERIOD_SEC = 30
CLAUDE_ALLOCATION_INTERVAL_SEC = 10
SIMULATOR_DEMAND_HISTORY_MAXLEN = 60

app = FastAPI(title="Opus Control API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

metrics_buffer: deque[dict[str, Any]] = deque(maxlen=BUFFER_MAX_ITEMS)
load_history: deque[tuple[float, float]] = deque(maxlen=60)
simulator_demand_history: deque[float] = deque(maxlen=SIMULATOR_DEMAND_HISTORY_MAXLEN)
current_allocation: float = 0.5
ws_connections: list[WebSocket] = []
last_auto_fix: dict[int, float] = {}


def get_redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379")


def _decode(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, bytes):
        return val.decode("utf-8")
    return str(val)


async def get_context(redis_client: aioredis.Redis) -> dict[str, Any]:
    """Load context from Redis; return defaults for missing keys."""
    out: dict[str, Any] = {
        "watch": [],
        "ignore": [],
        "thresholds": {"cpu_percent": DEFAULT_CPU_THRESHOLD, "mem_mb": DEFAULT_MEM_THRESHOLD_MB},
        "time_window_sec": 60,
    }
    try:
        watch_raw = await redis_client.get(CONTEXT_KEY_WATCH)
        if watch_raw is not None:
            out["watch"] = json.loads(_decode(watch_raw) or "[]")
        ignore_raw = await redis_client.get(CONTEXT_KEY_IGNORE)
        if ignore_raw is not None:
            out["ignore"] = json.loads(_decode(ignore_raw) or "[]")
        thresh_raw = await redis_client.get(CONTEXT_KEY_THRESHOLDS)
        if thresh_raw is not None:
            out["thresholds"] = json.loads(_decode(thresh_raw) or "{}")
            if "cpu_percent" not in out["thresholds"]:
                out["thresholds"]["cpu_percent"] = DEFAULT_CPU_THRESHOLD
            if "mem_mb" not in out["thresholds"]:
                out["thresholds"]["mem_mb"] = DEFAULT_MEM_THRESHOLD_MB
        tw_raw = await redis_client.get(CONTEXT_KEY_TIME_WINDOW)
        if tw_raw is not None:
            out["time_window_sec"] = int(float(_decode(tw_raw) or "60"))
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return out


async def record_override(
    redis_client: aioredis.Redis,
    pid: int,
    name: str,
    last_throttle: float | None,
    last_action: str,
) -> None:
    """Store user override for a process (throttle value or kill)."""
    field = str(pid)
    existing: dict[str, Any] = {}
    try:
        raw = await redis_client.hget(OVERRIDES_KEY, field)
        if raw is not None:
            existing = json.loads(_decode(raw) or "{}")
    except (json.JSONDecodeError, TypeError):
        pass
    existing["last_throttle"] = last_throttle
    existing["last_action"] = last_action
    existing["target_name"] = name or existing.get("target_name", "")
    existing["last_updated"] = time.time()
    await redis_client.hset(OVERRIDES_KEY, field, json.dumps(existing))


async def record_dismiss(
    redis_client: aioredis.Redis,
    target_pid: int,
    target_name: str,
) -> None:
    """Increment dismiss count for a process."""
    field = str(target_pid)
    existing: dict[str, Any] = {"dismiss_count": 0}
    try:
        raw = await redis_client.hget(OVERRIDES_KEY, field)
        if raw is not None:
            existing = json.loads(_decode(raw) or "{}")
    except (json.JSONDecodeError, TypeError):
        pass
    existing["dismiss_count"] = int(existing.get("dismiss_count", 0)) + 1
    existing["target_name"] = target_name or existing.get("target_name", "")
    existing["last_updated"] = time.time()
    await redis_client.hset(OVERRIDES_KEY, field, json.dumps(existing))


async def get_override(redis_client: aioredis.Redis, pid: int) -> dict[str, Any] | None:
    """Load override record for a process."""
    try:
        raw = await redis_client.hget(OVERRIDES_KEY, str(pid))
        if raw is None:
            return None
        return json.loads(_decode(raw) or "{}")
    except (json.JSONDecodeError, TypeError):
        return None


async def set_context(redis_client: aioredis.Redis, body: dict[str, Any]) -> None:
    """Persist context to Redis."""
    if "watch" in body and isinstance(body["watch"], list):
        await redis_client.set(CONTEXT_KEY_WATCH, json.dumps([str(x) for x in body["watch"]]))
    if "ignore" in body and isinstance(body["ignore"], list):
        await redis_client.set(CONTEXT_KEY_IGNORE, json.dumps([str(x) for x in body["ignore"]]))
    if "thresholds" in body and isinstance(body["thresholds"], dict):
        t = body["thresholds"]
        cpu = t.get("cpu_percent", DEFAULT_CPU_THRESHOLD)
        mem = t.get("mem_mb", DEFAULT_MEM_THRESHOLD_MB)
        await redis_client.set(
            CONTEXT_KEY_THRESHOLDS,
            json.dumps({"cpu_percent": max(0, min(100, int(cpu))), "mem_mb": max(0, int(mem))}),
        )
    if "time_window_sec" in body:
        try:
            tw = max(10, min(600, int(body["time_window_sec"])))
            await redis_client.set(CONTEXT_KEY_TIME_WINDOW, str(tw))
        except (TypeError, ValueError):
            pass


def build_metrics_for_analysis(
    buffer_snapshot: list[dict],
    context: dict[str, Any],
) -> list[dict]:
    """Apply time window, ignore list, watch list; return top 20 by CPU."""
    if not buffer_snapshot:
        return []
    watch = context.get("watch") or []
    ignore = context.get("ignore") or []
    time_window_sec = context.get("time_window_sec") or 60
    n = min(len(buffer_snapshot), max(1, int(time_window_sec / METRICS_BROADCAST_INTERVAL_SEC)))
    recent = buffer_snapshot[-n:]
    by_pid: dict[int, dict] = {}
    for m in recent:
        pid = m.get("pid")
        if pid is None:
            continue
        by_pid[pid] = m
    def is_ignored(proc: dict) -> bool:
        name = (proc.get("name") or "").lower()
        pid_str = str(proc.get("pid", ""))
        for ign in ignore:
            s = (ign or "").lower()
            if s in name or s == pid_str:
                return True
        return False
    candidates = [v for v in by_pid.values() if not is_ignored(v)]
    watch_set = {str(x).lower().strip() for x in watch if x}
    for v in by_pid.values():
        if v in candidates:
            continue
        name = (v.get("name") or "").lower()
        pid_str = str(v.get("pid", ""))
        if any(name == w or pid_str == w for w in watch_set) or any(w in name or w == pid_str for w in watch_set):
            candidates.append(v)
    return sorted(candidates, key=lambda m: m.get("cpu_percent", 0), reverse=True)[:20]


def _rule_based_anomaly(
    metrics: list[dict],
    cpu_threshold: float = DEFAULT_CPU_THRESHOLD,
    mem_threshold_mb: float = DEFAULT_MEM_THRESHOLD_MB,
) -> dict[str, Any] | None:
    """Fallback when no Anthropic API key: simple CPU/memory thresholds."""
    if not metrics:
        return None
    for m in metrics:
        cpu = m.get("cpu_percent", 0)
        mem = m.get("mem_mb", 0)
        if cpu > cpu_threshold:
            return {
                "reasoning_trace": f"Process {m.get('name', m.get('pid'))} (PID {m.get('pid')}) is using {cpu:.1f}% CPU.",
                "suggested_action": "Throttle CPU",
                "target_pid": m.get("pid"),
                "target_name": m.get("name", ""),
                "throttle_value": DEFAULT_THROTTLE_VALUE,
            }
        if mem > mem_threshold_mb:
            return {
                "reasoning_trace": f"Process {m.get('name', m.get('pid'))} (PID {m.get('pid')}) using {mem:.0f} MB may indicate memory pressure.",
                "suggested_action": "Throttle CPU",
                "target_pid": m.get("pid"),
                "target_name": m.get("name", ""),
                "throttle_value": DEFAULT_THROTTLE_VALUE,
            }
    return None


def _build_chart_base64(load_history_snapshot: list[tuple[float, float]]) -> str | None:
    """Build a simple line chart of system load over time; return PNG base64 or None."""
    if len(load_history_snapshot) < 2:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        xs = list(range(len(load_history_snapshot)))
        ys = [v for _, v in load_history_snapshot]
        fig, ax = plt.subplots(figsize=(6, 2.5))
        ax.plot(xs, ys, color="#00ff88", linewidth=1.5)
        ax.set_ylim(0, max(100, max(ys) * 1.1))
        ax.set_xlabel("Time step")
        ax.set_ylabel("Avg CPU %")
        ax.set_title("System load (top processes)")
        ax.set_facecolor("#0f1629")
        fig.patch.set_facecolor("#0f1629")
        ax.tick_params(colors="#6b7a99")
        ax.spines["bottom"].set_color("#1a2744")
        ax.spines["left"].set_color("#1a2744")
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=80, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return base64.standard_b64encode(buf.read()).decode("ascii")
    except Exception:
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
    context: dict[str, Any] | None = None,
    load_history_snapshot: list[tuple[float, float]] | None = None,
) -> dict[str, Any] | None:
    """Run anomaly detection via Claude API or rule-based fallback; auto-publish throttle if applicable."""
    if not metrics:
        return None

    thresholds = (context or {}).get("thresholds") or {}
    cpu_threshold = float(thresholds.get("cpu_percent", DEFAULT_CPU_THRESHOLD))
    mem_threshold_mb = float(thresholds.get("mem_mb", DEFAULT_MEM_THRESHOLD_MB))

    api_key = os.getenv("ANTHROPIC_API_KEY")
    result: dict[str, Any] | None = None

    if api_key:
        try:
            from anthropic import AsyncAnthropic

            top = sorted(metrics, key=lambda m: m.get("cpu_percent", 0), reverse=True)[:20]
            metrics_json = json.dumps(top, indent=0)

            chart_b64 = None
            if load_history_snapshot:
                chart_b64 = _build_chart_base64(load_history_snapshot)

            prompt_text = f"""You are an anomaly detector for system metrics. Given the following list of processes (pid, name, cpu_percent, mem_mb), identify at most one critical issue: high CPU (above {cpu_threshold}%) or very high memory (above {mem_threshold_mb} MB).
""" + (
                "\nThe image shows a short time-series of system load (avg CPU). Use it together with the metrics JSON to confirm anomalies and suggest actions.\n\n"
                if chart_b64
                else "\n"
            ) + """You must choose how to fix it yourself:
- "Throttle CPU": reduce the process priority. Include "throttle_value" between 0.0 and 1.0 (0 = most throttled, 1 = normal). Use lower values (e.g. 0.2-0.4) for severe CPU hogging, higher (e.g. 0.5-0.7) for mild issues.
- "Kill": terminate the process. Use only for runaway or clearly non-essential processes when throttling is not enough.

Respond with exactly one JSON object, no other text.

If there is an anomaly and you choose Throttle CPU:
{{"anomaly": true, "reasoning_trace": "brief explanation", "suggested_action": "Throttle CPU", "throttle_value": <0.0-1.0>, "target_pid": <pid number>, "target_name": "<process name>"}}

If there is an anomaly and you choose Kill:
{{"anomaly": true, "reasoning_trace": "brief explanation", "suggested_action": "Kill", "target_pid": <pid number>, "target_name": "<process name>"}}

If there is no critical issue: {{"anomaly": false}}

Metrics (JSON):
""" + metrics_json

            content: list[dict[str, Any]] = []
            if chart_b64:
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": chart_b64},
                })
            content.append({"type": "text", "text": prompt_text})

            client = AsyncAnthropic(api_key=api_key)
            message = await client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=512,
                messages=[{"role": "user", "content": content}],
            )
            content = message.content
            if content and len(content) > 0:
                block = content[0]
                text = getattr(block, "text", None) or (block.get("text") if isinstance(block, dict) else None) or str(content)
            else:
                text = str(content)
            parsed = _parse_claude_json(text)
            if parsed and parsed.get("anomaly") and parsed.get("target_pid") is not None:
                action = parsed.get("suggested_action") or "Throttle CPU"
                result = {
                    "reasoning_trace": parsed.get("reasoning_trace", "Anomaly detected."),
                    "suggested_action": action,
                    "target_pid": int(parsed["target_pid"]),
                    "target_name": parsed.get("target_name", ""),
                }
                if action == "Throttle CPU":
                    tv = parsed.get("throttle_value")
                    if tv is not None:
                        try:
                            result["throttle_value"] = max(0.0, min(1.0, float(tv)))
                        except (TypeError, ValueError):
                            result["throttle_value"] = DEFAULT_THROTTLE_VALUE
                    else:
                        result["throttle_value"] = DEFAULT_THROTTLE_VALUE
        except Exception:
            result = _rule_based_anomaly(metrics, cpu_threshold, mem_threshold_mb)
    else:
        result = _rule_based_anomaly(metrics, cpu_threshold, mem_threshold_mb)

    if result is None:
        return None

    auto_fix_applied = False
    if result.get("target_pid") is not None and redis_client is not None:
        pid = result["target_pid"]
        now = time.monotonic()
        if now - last_auto_fix.get(pid, 0) >= AUTO_FIX_COOLDOWN_SEC:
            action = result.get("suggested_action") or "Throttle CPU"
            if action == "Throttle CPU":
                throttle_val = result.get("throttle_value", DEFAULT_THROTTLE_VALUE)
                throttle_val = max(0.0, min(1.0, float(throttle_val)))
                command = f"throttle:{pid}:{throttle_val}"
                await redis_client.publish(COMMANDS_CHANNEL, command)
                last_auto_fix[pid] = now
                auto_fix_applied = True
            elif action == "Kill":
                command = f"kill:{pid}"
                await redis_client.publish(COMMANDS_CHANNEL, command)
                last_auto_fix[pid] = now
                auto_fix_applied = True

    result["auto_fix_applied"] = auto_fix_applied

    if redis_client is not None and result.get("target_pid") is not None:
        override = await get_override(redis_client, result["target_pid"])
        if override:
            if override.get("last_action") == "throttle" and override.get("last_throttle") is not None:
                try:
                    result["user_usual_throttle"] = max(0.0, min(1.0, float(override["last_throttle"])))
                except (TypeError, ValueError):
                    pass
            result["dismiss_count"] = int(override.get("dismiss_count", 0))
            if result["dismiss_count"] >= DISMISS_SUGGEST_THRESHOLD:
                result["suggest_reduce_alerts"] = True

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
        context = await get_context(redis_client)
        metrics_for_claude = build_metrics_for_analysis(snapshot, context)
        if not metrics_for_claude:
            continue
        avg_load = sum(m.get("cpu_percent", 0) for m in metrics_for_claude) / len(metrics_for_claude)
        load_history.append((time.monotonic(), avg_load))
        result = await analyze_with_claude(
            metrics_for_claude,
            redis_client,
            context,
            load_history_snapshot=list(load_history),
        )
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


def _cosine_demand(t: float, period: float = SIMULATOR_COSINE_PERIOD_SEC) -> float:
    """Demand in [0, 100] from cosine wave."""
    val = 50 + 40 * math.cos(2 * math.pi * t / period)
    return max(0.0, min(100.0, val))


async def simulator_broadcast_loop() -> None:
    """Every 1.5s compute cosine demand, append to history, broadcast simulator_tick."""
    global current_allocation
    while True:
        await asyncio.sleep(METRICS_BROADCAST_INTERVAL_SEC)
        t = time.monotonic()
        demand = _cosine_demand(t)
        simulator_demand_history.append(demand)
        if ws_connections:
            msg = json.dumps({
                "type": "simulator_tick",
                "demand": round(demand, 2),
                "allocation": round(current_allocation, 4),
            })
            for ws in list(ws_connections):
                try:
                    await ws.send_text(msg)
                except Exception:
                    pass


async def broadcast_allocation_update(allocation: float) -> None:
    """Notify all WebSocket clients of a new allocation value."""
    if not ws_connections:
        return
    msg = json.dumps({"type": "allocation_update", "allocation": round(allocation, 4)})
    for ws in list(ws_connections):
        try:
            await ws.send_text(msg)
        except Exception:
            pass


def _parse_allocation_json(text: str) -> float | None:
    """Extract allocation (0-1) from Claude response."""
    text = (text or "").strip()
    if "```" in text:
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    if start == -1:
        return None
    try:
        obj = json.loads(text[start:])
        a = obj.get("allocation")
        if a is not None:
            return max(0.0, min(1.0, float(a)))
    except (json.JSONDecodeError, TypeError, ValueError, KeyError):
        pass
    return None


async def claude_allocation_loop() -> None:
    """Every N seconds ask Claude for suggested allocation; update and broadcast."""
    global current_allocation
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return
    while True:
        await asyncio.sleep(CLAUDE_ALLOCATION_INTERVAL_SEC)
        history_snapshot = list(simulator_demand_history)
        if len(history_snapshot) < 2:
            continue
        try:
            from anthropic import AsyncAnthropic
            last_n = history_snapshot[-20:]
            prompt = f"""You are a resource allocator. The demand (resource need) is a time series that oscillates (cosine). Current demand values (last {len(last_n)} points): {last_n}. The current allocation setpoint (0 = low, 1 = high) is {current_allocation}. Suggest an allocation value 0-1 to match demand or smooth usage. Respond with a single JSON: {{"allocation": number}}."""
            client = AsyncAnthropic(api_key=api_key)
            message = await client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=128,
                messages=[{"role": "user", "content": prompt}],
            )
            content = message.content
            if content and len(content) > 0:
                block = content[0]
                text = getattr(block, "text", None) or (block.get("text") if isinstance(block, dict) else None) or str(content)
            else:
                text = ""
            suggested = _parse_allocation_json(text)
            if suggested is not None:
                current_allocation = suggested
                await broadcast_allocation_update(current_allocation)
        except Exception:
            pass


@app.on_event("startup")
async def startup() -> None:
    redis_client = aioredis.from_url(get_redis_url(), decode_responses=False)
    app.state.redis = redis_client
    asyncio.create_task(stream_consumer(redis_client))
    asyncio.create_task(analysis_loop(redis_client))
    asyncio.create_task(metrics_broadcast_loop())
    asyncio.create_task(simulator_broadcast_loop())
    asyncio.create_task(claude_allocation_loop())


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
                redis_client = app.state.redis
                if data.get("type") == "apply_fix" and "command" in data:
                    cmd = data["command"]
                    await redis_client.publish(COMMANDS_CHANNEL, cmd)
                    if cmd.startswith("throttle:") and cmd.count(":") >= 2:
                        parts = cmd.split(":", 2)
                        try:
                            pid = int(parts[1])
                            throttle_val = float(parts[2])
                            name = ""
                            for m in metrics_buffer:
                                if m.get("pid") == pid:
                                    name = m.get("name", "") or name
                            await record_override(redis_client, pid, name, throttle_val, "throttle")
                        except (ValueError, IndexError):
                            pass
                    elif cmd.startswith("kill:"):
                        try:
                            pid = int(cmd.split(":", 1)[1])
                            name = ""
                            for m in metrics_buffer:
                                if m.get("pid") == pid:
                                    name = m.get("name", "") or name
                            await record_override(redis_client, pid, name, None, "kill")
                        except (ValueError, IndexError):
                            pass
                elif data.get("type") == "dismiss_anomaly":
                    target_pid = data.get("target_pid")
                    target_name = data.get("target_name", "") or ""
                    if target_pid is not None:
                        await record_dismiss(redis_client, int(target_pid), target_name)
                elif data.get("type") == "set_allocation":
                    try:
                        val = float(data.get("allocation", 0.5))
                        global current_allocation
                        current_allocation = max(0.0, min(1.0, val))
                        await broadcast_allocation_update(current_allocation)
                    except (TypeError, ValueError):
                        pass
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


@app.get("/api/context")
async def api_get_context() -> dict[str, Any]:
    redis_client = app.state.redis
    return await get_context(redis_client)


@app.put("/api/context")
async def api_put_context(body: dict[str, Any] = Body(...)) -> dict[str, str]:
    redis_client = app.state.redis
    await set_context(redis_client, body)
    return {"status": "ok"}


@app.post("/api/rephrase-reasoning")
async def api_rephrase_reasoning(body: dict[str, Any] = Body(...)) -> dict[str, str]:
    """Ask Claude to rephrase the anomaly reasoning; returns new reasoning_trace."""
    reasoning = body.get("reasoning_trace") or "Anomaly detected."
    instruction = body.get("instruction") or "same length"
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return {"reasoning_trace": reasoning}
    try:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=api_key)
        message = await client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=256,
            messages=[
                {
                    "role": "user",
                    "content": f"""Rephrase this anomaly explanation for the user. Keep the same meaning. Instruction: {instruction}.

Original explanation:
{reasoning}

Respond with only the new explanation, no other text.""",
                }
            ],
        )
        content = message.content
        if content and len(content) > 0:
            block = content[0]
            text = getattr(block, "text", None) or (block.get("text") if isinstance(block, dict) else None) or str(content)
            text = (text or "").strip() or reasoning
        else:
            text = reasoning
        return {"reasoning_trace": text}
    except Exception:
        return {"reasoning_trace": reasoning}


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
