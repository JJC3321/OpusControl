import { useCallback, useEffect, useRef, useState } from "react";
import type { AnomalyPayload, ProcessMetric, WsMessage } from "../types";

const WS_URL = "ws://localhost:8000/ws";

const SIMULATOR_HISTORY_MAX = 60;

function formatTime(ms: number) {
  const d = new Date(ms);
  return d.toLocaleTimeString("en-US", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function useWebSocket() {
  const [connected, setConnected] = useState(false);
  const [metrics, setMetrics] = useState<ProcessMetric[]>([]);
  const [anomaly, setAnomaly] = useState<AnomalyPayload | null>(null);
  const [simulatorDemandHistory, setSimulatorDemandHistory] = useState<
    { time: string; demand: number }[]
  >([]);
  const [allocation, setAllocation] = useState(0.5);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout>>();

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => setConnected(true);
    ws.onclose = () => {
      setConnected(false);
      reconnectTimeoutRef.current = setTimeout(connect, 2000);
    };
    ws.onerror = () => {};
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as WsMessage;
        if (data.type === "metrics") setMetrics(data.payload);
        if (data.type === "anomaly") setAnomaly(data.payload);
        if (data.type === "simulator_tick") {
          setSimulatorDemandHistory((prev) => {
            const next = [
              ...prev,
              { time: formatTime(Date.now()), demand: data.demand },
            ];
            return next.length > SIMULATOR_HISTORY_MAX
              ? next.slice(-SIMULATOR_HISTORY_MAX)
              : next;
          });
          setAllocation(data.allocation);
        }
        if (data.type === "allocation_update") setAllocation(data.allocation);
      } catch {
        // ignore parse errors
      }
    };
  }, []);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [connect]);

  const sendApplyFix = useCallback((command: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "apply_fix", command }));
    }
  }, []);

  const sendDismissAnomaly = useCallback((payload: AnomalyPayload) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          type: "dismiss_anomaly",
          target_pid: payload.target_pid,
          target_name: payload.target_name ?? "",
        })
      );
    }
  }, []);

  const clearAnomaly = useCallback(() => setAnomaly(null), []);

  const sendSetAllocation = useCallback((value: number) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      const clamped = Math.max(0, Math.min(1, value));
      wsRef.current.send(
        JSON.stringify({ type: "set_allocation", allocation: clamped })
      );
    }
  }, []);

  return {
    connected,
    metrics,
    anomaly,
    sendApplyFix,
    sendDismissAnomaly,
    clearAnomaly,
    simulatorDemandHistory,
    allocation,
    sendSetAllocation,
  };
}
