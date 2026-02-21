export interface ProcessMetric {
  pid: number;
  cpu_percent: number;
  mem_mb: number;
  name: string;
}

export interface AnomalyPayload {
  reasoning_trace: string;
  suggested_action: string;
  target_pid: number;
  target_name: string;
  auto_fix_applied?: boolean;
}

export type WsMessage =
  | { type: "connected"; message: string }
  | { type: "metrics"; payload: ProcessMetric[] }
  | { type: "anomaly"; payload: AnomalyPayload };
