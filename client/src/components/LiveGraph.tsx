import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { useEffect, useState } from "react";
import type { ProcessMetric } from "../types";

const MAX_POINTS = 60;

interface LiveGraphProps {
  metrics: ProcessMetric[];
}

interface DataPoint {
  time: string;
  load: number;
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

export function LiveGraph({ metrics }: LiveGraphProps) {
  const [history, setHistory] = useState<DataPoint[]>([]);

  useEffect(() => {
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
  }, [metrics]);

  const data = history.length ? history : [{ time: formatTime(Date.now()), load: 0 }];

  return (
    <div className="h-64 rounded border border-hud-border bg-hud-panel/80 p-3">
      <div className="mb-1 text-xs text-hud-dim">SYSTEM LOAD</div>
      <ResponsiveContainer width="100%" height="90%">
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
            formatter={(value: number) => [`${value}%`, "Load"]}
          />
          <Area
            type="monotone"
            dataKey="load"
            stroke="#00ff88"
            strokeWidth={1.5}
            fill="url(#loadGrad)"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
