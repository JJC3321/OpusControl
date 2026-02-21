import { useEffect, useState } from "react";
import type { AnomalyPayload } from "../types";

const API_BASE = "";

interface FlashAlertProps {
  payload: AnomalyPayload;
  onApplyFix: (command: string) => void;
  onDismiss: (payload: AnomalyPayload) => void;
  onClose: () => void;
}

function getDefaultThrottle(payload: AnomalyPayload): number {
  if (payload.suggested_action !== "Throttle CPU") return 0.5;
  if (payload.user_usual_throttle != null) return payload.user_usual_throttle;
  if (payload.throttle_value != null) return payload.throttle_value;
  return 0.5;
}

export function FlashAlert({ payload, onApplyFix, onDismiss, onClose }: FlashAlertProps) {
  const [throttleValue, setThrottleValue] = useState(getDefaultThrottle(payload));
  const [editedReasoning, setEditedReasoning] = useState(payload.reasoning_trace);
  const isKill = payload.suggested_action === "Kill";
  const [reducingAlerts, setReducingAlerts] = useState(false);

  useEffect(() => {
    setEditedReasoning(payload.reasoning_trace);
  }, [payload.reasoning_trace]);

  const handleApplyThrottle = () => {
    onApplyFix(`throttle:${payload.target_pid}:${throttleValue}`);
    onClose();
  };

  const handleKill = () => {
    onApplyFix(`kill:${payload.target_pid}`);
    onClose();
  };

  const handleReduceAlerts = async () => {
    const name = payload.target_name || String(payload.target_pid);
    try {
      const r = await fetch(`${API_BASE}/api/context`);
      const ctx = await r.json();
      const ignore = Array.isArray(ctx.ignore) ? [...ctx.ignore, name] : [name];
      await fetch(`${API_BASE}/api/context`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...ctx, ignore }),
      });
      setReducingAlerts(true);
    } catch {
      // ignore
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70"
      role="dialog"
      aria-label="Anomaly alert"
    >
      <div className="w-full max-w-md rounded border-2 border-hud-red bg-hud-panel p-6 shadow-lg shadow-hud-red/20">
        <div className="mb-2 text-sm font-medium uppercase tracking-wider text-hud-red">
          Anomaly detected
        </div>
        {payload.auto_fix_applied && (
          <p className="mb-2 text-xs text-hud-green">Fix was applied automatically (Claude&apos;s choice).</p>
        )}
        <label className="mb-1 block text-xs text-hud-dim">Reasoning</label>
        <textarea
          rows={3}
          value={editedReasoning}
          onChange={(e) => setEditedReasoning(e.target.value)}
          className="mb-4 w-full resize-y rounded border border-hud-border bg-hud-bg px-2 py-1 text-sm text-hud-dim focus:border-hud-green focus:outline-none"
        />
        <p className="mb-4 text-hud-green">
          Suggested action: <span className="font-medium">{payload.suggested_action}</span>
          {payload.target_name && (
            <span className="text-hud-dim"> ({payload.target_name}, PID {payload.target_pid})</span>
          )}
        </p>
        {isKill ? (
          <div className="mb-4 text-xs text-hud-dim">
            Manual override: kill this process.
          </div>
        ) : (
          <div className="mb-4">
            {payload.user_usual_throttle != null && (
              <p className="mb-1 text-xs text-hud-green">You usually use {payload.user_usual_throttle.toFixed(2)} for this process.</p>
            )}
            <label className="mb-1 block text-xs text-hud-dim">
              Throttle value (0 = low, 1 = normal): {throttleValue.toFixed(2)}
              {payload.throttle_value != null && payload.user_usual_throttle == null && (
                <span className="ml-1 text-hud-green"> (Claude chose {payload.throttle_value.toFixed(2)})</span>
              )}
            </label>
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              value={throttleValue}
              onChange={(e) => setThrottleValue(Number(e.target.value))}
              className="w-full accent-hud-green"
            />
          </div>
        )}
        <div className="flex gap-3">
          {isKill ? (
            <button
              type="button"
              onClick={handleKill}
              className="rounded border border-hud-red bg-hud-red/10 px-4 py-2 text-sm font-medium text-hud-red hover:bg-hud-red/20"
            >
              Kill process
            </button>
          ) : (
            <button
              type="button"
              onClick={handleApplyThrottle}
              className="rounded border border-hud-green bg-hud-green/10 px-4 py-2 text-sm font-medium text-hud-green hover:bg-hud-green/20"
            >
              Apply fix
            </button>
          )}
          {payload.suggest_reduce_alerts && !reducingAlerts && (
            <button
              type="button"
              onClick={handleReduceAlerts}
              className="rounded border border-hud-border px-4 py-2 text-xs text-hud-dim hover:border-hud-red hover:text-hud-red"
            >
              Reduce future alerts for this process
            </button>
          )}
          {reducingAlerts && (
            <span className="text-xs text-hud-green">Added to ignore list.</span>
          )}
          <button
            type="button"
            onClick={() => onDismiss(payload)}
            className="rounded border border-hud-border px-4 py-2 text-sm text-hud-dim hover:border-hud-dim hover:text-white"
          >
            Dismiss
          </button>
        </div>
      </div>
    </div>
  );
}
