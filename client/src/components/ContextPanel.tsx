import { useCallback, useEffect, useRef, useState } from "react";

const API_BASE = "";

export interface ContextConfig {
  watch: string[];
  ignore: string[];
  thresholds: { cpu_percent: number; mem_mb: number };
  time_window_sec: number;
}

const DEFAULT_CONTEXT: ContextConfig = {
  watch: [],
  ignore: [],
  thresholds: { cpu_percent: 90, mem_mb: 1500 },
  time_window_sec: 60,
};

const TIME_WINDOW_OPTIONS = [
  { label: "Last 30s", value: 30 },
  { label: "Last 1m", value: 60 },
  { label: "Last 2m", value: 120 },
  { label: "Last 5m", value: 300 },
];

function TagList({
  items,
  onAdd,
  onRemove,
  placeholder,
}: {
  items: string[];
  onAdd: (value: string) => void;
  onRemove: (index: number) => void;
  placeholder: string;
}) {
  const [input, setInput] = useState("");

  const handleAdd = useCallback(() => {
    const v = input.trim();
    if (v && !items.includes(v)) {
      onAdd(v);
      setInput("");
    }
  }, [input, items, onAdd]);

  return (
    <div className="space-y-1">
      <div className="flex flex-wrap gap-1">
        {items.map((item, i) => (
          <span
            key={`${item}-${i}`}
            className="inline-flex items-center rounded border border-hud-border bg-hud-panel px-2 py-0.5 text-xs text-hud-green"
          >
            {item}
            <button
              type="button"
              onClick={() => onRemove(i)}
              className="ml-1 text-hud-dim hover:text-hud-red"
              aria-label={`Remove ${item}`}
            >
              x
            </button>
          </span>
        ))}
      </div>
      <div className="flex gap-1">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), handleAdd())}
          placeholder={placeholder}
          className="flex-1 rounded border border-hud-border bg-hud-bg px-2 py-1 text-sm text-hud-green placeholder:text-hud-dim focus:border-hud-green focus:outline-none"
        />
        <button
          type="button"
          onClick={handleAdd}
          className="rounded border border-hud-border px-2 py-1 text-xs text-hud-dim hover:border-hud-green hover:text-hud-green"
        >
          Add
        </button>
      </div>
    </div>
  );
}

export function ContextPanel() {
  const [config, setConfig] = useState<ContextConfig>(DEFAULT_CONTEXT);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const putTimeoutRef = useRef<ReturnType<typeof setTimeout>>();

  const fetchContext = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/context`);
      if (r.ok) {
        const data = await r.json();
        setConfig({
          watch: Array.isArray(data.watch) ? data.watch : [],
          ignore: Array.isArray(data.ignore) ? data.ignore : [],
          thresholds:
            data.thresholds && typeof data.thresholds === "object"
              ? {
                  cpu_percent: Number(data.thresholds.cpu_percent) || 90,
                  mem_mb: Number(data.thresholds.mem_mb) || 1500,
                }
              : DEFAULT_CONTEXT.thresholds,
          time_window_sec: Number(data.time_window_sec) || 60,
        });
      }
    } catch {
      // keep current state
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchContext();
  }, [fetchContext]);

  const putContext = useCallback((next: ContextConfig) => {
    if (putTimeoutRef.current) clearTimeout(putTimeoutRef.current);
    putTimeoutRef.current = setTimeout(async () => {
      try {
        await fetch(`${API_BASE}/api/context`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(next),
        });
      } catch {
        // ignore
      }
      putTimeoutRef.current = undefined;
    }, 400);
  }, []);

  const updateConfig = useCallback(
    (patch: Partial<ContextConfig>) => {
      const next = { ...config, ...patch };
      setConfig(next);
      putContext(next);
    },
    [config, putContext]
  );

  const addWatch = useCallback(
    (v: string) => {
      const next = { ...config, watch: [...config.watch, v] };
      setConfig(next);
      putContext(next);
    },
    [config, putContext]
  );
  const removeWatch = useCallback(
    (i: number) => {
      const next = { ...config, watch: config.watch.filter((_, idx) => idx !== i) };
      setConfig(next);
      putContext(next);
    },
    [config, putContext]
  );
  const addIgnore = useCallback(
    (v: string) => {
      const next = { ...config, ignore: [...config.ignore, v] };
      setConfig(next);
      putContext(next);
    },
    [config, putContext]
  );
  const removeIgnore = useCallback(
    (i: number) => {
      const next = { ...config, ignore: config.ignore.filter((_, idx) => idx !== i) };
      setConfig(next);
      putContext(next);
    },
    [config, putContext]
  );

  return (
    <section className="rounded border border-hud-border bg-hud-panel/80">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-hud-dim hover:text-hud-green"
      >
        Context
        <span className="text-hud-dim">{open ? "-" : "+"}</span>
      </button>
      {open && (
        <div className="border-t border-hud-border p-4 space-y-4">
          {loading ? (
            <p className="text-xs text-hud-dim">Loading...</p>
          ) : (
            <>
              <div>
                <label className="mb-1 block text-xs text-hud-dim">Watch list (always include)</label>
                <TagList
                  items={config.watch}
                  onAdd={addWatch}
                  onRemove={removeWatch}
                  placeholder="Process name or PID"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-hud-dim">Ignore list (exclude from analysis)</label>
                <TagList
                  items={config.ignore}
                  onAdd={addIgnore}
                  onRemove={removeIgnore}
                  placeholder="Process name or substring"
                />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="mb-1 block text-xs text-hud-dim">CPU anomaly above (%)</label>
                  <input
                    type="range"
                    min="50"
                    max="100"
                    value={config.thresholds.cpu_percent}
                    onChange={(e) =>
                      updateConfig({
                        thresholds: {
                          ...config.thresholds,
                          cpu_percent: Number(e.target.value),
                        },
                      })
                    }
                    className="w-full accent-hud-green"
                  />
                  <span className="text-xs text-hud-green">{config.thresholds.cpu_percent}%</span>
                </div>
                <div>
                  <label className="mb-1 block text-xs text-hud-dim">Memory anomaly above (MB)</label>
                  <input
                    type="number"
                    min="100"
                    max="10000"
                    step="100"
                    value={config.thresholds.mem_mb}
                    onChange={(e) =>
                      updateConfig({
                        thresholds: {
                          ...config.thresholds,
                          mem_mb: Number(e.target.value) || 1500,
                        },
                      })
                    }
                    className="w-full rounded border border-hud-border bg-hud-bg px-2 py-1 text-sm text-hud-green focus:border-hud-green focus:outline-none"
                  />
                </div>
              </div>
              <div>
                <label className="mb-1 block text-xs text-hud-dim">Time window</label>
                <select
                  value={config.time_window_sec}
                  onChange={(e) => updateConfig({ time_window_sec: Number(e.target.value) })}
                  className="w-full rounded border border-hud-border bg-hud-bg px-2 py-1 text-sm text-hud-green focus:border-hud-green focus:outline-none"
                >
                  {TIME_WINDOW_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}
