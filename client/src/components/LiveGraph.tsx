import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ProcessMetric } from "../types";

const MAX_POINTS = 60;

interface DataPoint {
  time: string;
  load: number;
}

interface LiveGraphProps {
  metrics: ProcessMetric[];
  onApplyThrottle?: (command: string) => void;
  simulatorDemandHistory?: { time: string; demand: number }[];
  allocation?: number;
  onAllocationChange?: (value: number) => void;
}

function formatTime(ms: number) {
  const d = new Date(ms);
  return d.toLocaleTimeString("en-US", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function LiveGraph({
  metrics,
  onApplyThrottle,
  simulatorDemandHistory,
  allocation: allocationProp,
  onAllocationChange,
}: LiveGraphProps) {
  const [history, setHistory] = useState<DataPoint[]>([]);
  const [localAllocation, setLocalAllocation] = useState(0.5);
  const [isDragging, setIsDragging] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const isSimulatorMode =
    simulatorDemandHistory != null && simulatorDemandHistory.length > 0;
  const allocationDisplay = isSimulatorMode
    ? (allocationProp ?? 0.5)
    : localAllocation;
  const canDrag = isSimulatorMode ? !!onAllocationChange : !!onApplyThrottle;

  useEffect(() => {
    if (isSimulatorMode) return;
    const load = metrics.length
      ? metrics.reduce((sum, m) => sum + (m.cpu_percent ?? 0), 0) / metrics.length
      : 0;
    const point: DataPoint = {
      time: formatTime(Date.now()),
      load: Math.round(load * 10) / 10,
    };
    setHistory((prev) => {
      const next = [...prev, point];
      return next.length > MAX_POINTS ? next.slice(-MAX_POINTS) : next;
    });
  }, [metrics, isSimulatorMode]);

  const data = isSimulatorMode
    ? simulatorDemandHistory!.map(({ time, demand }) => ({ time, load: demand }))
    : history.length
      ? history
      : [{ time: formatTime(Date.now()), load: 0 }];

  const topProcess =
    !isSimulatorMode && metrics.length
      ? [...metrics].sort((a, b) => (b.cpu_percent ?? 0) - (a.cpu_percent ?? 0))[0]
      : null;

  const sendThrottle = useCallback(
    (value: number) => {
      if (onApplyThrottle && topProcess) {
        const clamped = Math.max(0, Math.min(1, value));
        onApplyThrottle(`throttle:${topProcess.pid}:${clamped.toFixed(2)}`);
      }
    },
    [onApplyThrottle, topProcess]
  );

  const handlePointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (!containerRef.current || !canDrag) return;
      e.preventDefault();
      setIsDragging(true);
      const rect = containerRef.current.getBoundingClientRect();
      const y = 1 - (e.clientY - rect.top) / rect.height;
      const a = Math.max(0, Math.min(1, y));
      if (isSimulatorMode && onAllocationChange) {
        onAllocationChange(a);
      } else {
        setLocalAllocation(a);
        sendThrottle(a);
      }
      (e.target as HTMLElement).setPointerCapture(e.pointerId);
    },
    [canDrag, isSimulatorMode, onAllocationChange, sendThrottle]
  );

  const handlePointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (!isDragging || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const y = 1 - (e.clientY - rect.top) / rect.height;
      const a = Math.max(0, Math.min(1, y));
      if (isSimulatorMode && onAllocationChange) {
        onAllocationChange(a);
      } else {
        setLocalAllocation(a);
        sendThrottle(a);
      }
    },
    [isDragging, isSimulatorMode, onAllocationChange, sendThrottle]
  );

  const handlePointerUp = useCallback(
    (e: React.PointerEvent) => {
      if (isDragging) {
        setIsDragging(false);
        (e.target as HTMLElement).releasePointerCapture(e.pointerId);
      }
    },
    [isDragging]
  );

  return (
    <div className="rounded border border-hud-border bg-hud-panel/80 p-3">
      <div className="mb-1 flex items-center justify-between text-xs text-hud-dim">
        <span>{isSimulatorMode ? "DEMAND (cosine)" : "SYSTEM LOAD"}</span>
        {canDrag && (
          <span className="text-hud-green">
            Drag line to allocate resources (top = more, bottom = less)
            {!isSimulatorMode && topProcess &&
              ` · Top: ${topProcess.name} (PID ${topProcess.pid})`}
            {isSimulatorMode && " · Claude can adjust the line"}
          </span>
        )}
      </div>
      <div
        ref={containerRef}
        className="relative h-64 select-none"
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerLeave={handlePointerUp}
      >
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="loadGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#00ff88" stopOpacity={0.4} />
                <stop offset="100%" stopColor="#00ff88" stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis
              dataKey="time"
              tick={{ fill: "#6b7a99", fontSize: 10 }}
              axisLine={{ stroke: "#1a2744" }}
              tickLine={false}
            />
            <YAxis
              domain={[0, 100]}
              tick={{ fill: "#6b7a99", fontSize: 10 }}
              axisLine={{ stroke: "#1a2744" }}
              tickLine={false}
              width={32}
            />
            <Tooltip
              contentStyle={{
                background: "#0f1629",
                border: "1px solid #1a2744",
                borderRadius: "4px",
                color: "#00ff88",
              }}
              formatter={(value: number) =>
                [`${value}%`, isSimulatorMode ? "Demand" : "Load"]
              }
            />
            <Area
              type="monotone"
              dataKey="load"
              stroke="#00ff88"
              strokeWidth={1.5}
              fill="url(#loadGrad)"
            />
            {canDrag && (
              <ReferenceLine
                y={allocationDisplay * 100}
                stroke="#00ff88"
                strokeWidth={2}
                strokeDasharray="4 4"
              />
            )}
          </AreaChart>
        </ResponsiveContainer>
        {canDrag && (
          <div
            role="slider"
            aria-label="Resource allocation"
            aria-valuenow={allocationDisplay}
            aria-valuemin={0}
            aria-valuemax={1}
            className="absolute left-0 right-0 top-0 bottom-0 cursor-ns-resize"
            style={{ touchAction: "none" }}
            onPointerDown={handlePointerDown}
          />
        )}
      </div>
    </div>
  );
}
