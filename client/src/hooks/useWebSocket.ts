import { useCallback, useEffect, useRef, useState } from "react";
import type { AnomalyPayload, ProcessMetric, WsMessage } from "../types";

const WS_URL = "ws://localhost:8000/ws";

export function useWebSocket() {
  const [connected, setConnected] = useState(false);
  const [metrics, setMetrics] = useState<ProcessMetric[]>([]);
  const [anomaly, setAnomaly] = useState<AnomalyPayload | null>(null);
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

  const clearAnomaly = useCallback(() => setAnomaly(null), []);

  return { connected, metrics, anomaly, sendApplyFix, clearAnomaly };
}
