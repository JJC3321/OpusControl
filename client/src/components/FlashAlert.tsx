import { useState } from "react";
import type { AnomalyPayload } from "../types";

interface FlashAlertProps {
  payload: AnomalyPayload;
  onApplyFix: (command: string) => void;
  onDismiss: () => void;
}

export function FlashAlert({ payload, onApplyFix, onDismiss }: FlashAlertProps) {
  const [throttleValue, setThrottleValue] = useState(0.5);

  const handleApply = () => {
    onApplyFix(`throttle:${payload.target_pid}:${throttleValue}`);
    onDismiss();
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
          <p className="mb-2 text-xs text-hud-green">Fix was applied automatically.</p>
        )}
        <p className="mb-4 text-sm text-hud-dim">{payload.reasoning_trace}</p>
        <p className="mb-4 text-hud-green">
          Suggested action: <span className="font-medium">{payload.suggested_action}</span>
          {payload.target_name && (
            <span className="text-hud-dim"> ({payload.target_name}, PID {payload.target_pid})</span>
          )}
        </p>
        <div className="mb-4">
          <label className="mb-1 block text-xs text-hud-dim">
            Throttle value (0 = low, 1 = normal): {throttleValue.toFixed(2)}
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
        <div className="flex gap-3">
          <button
            type="button"
            onClick={handleApply}
            className="rounded border border-hud-green bg-hud-green/10 px-4 py-2 text-sm font-medium text-hud-green hover:bg-hud-green/20"
          >
            Apply fix
          </button>
          <button
            type="button"
            onClick={onDismiss}
            className="rounded border border-hud-border px-4 py-2 text-sm text-hud-dim hover:border-hud-dim hover:text-white"
          >
            Dismiss
          </button>
        </div>
      </div>
    </div>
  );
}
