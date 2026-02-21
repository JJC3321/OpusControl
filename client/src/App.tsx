import { LiveGraph } from "./components/LiveGraph";
import { FlashAlert } from "./components/FlashAlert";
import { useWebSocket } from "./hooks/useWebSocket";

function App() {
  const { connected, metrics, anomaly, sendApplyFix, clearAnomaly } = useWebSocket();

  return (
    <div className="min-h-screen bg-hud-bg text-hud-green">
      <header className="border-b border-hud-border px-6 py-4">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-bold tracking-wider">Opus Control</h1>
          <span
            className={`rounded px-2 py-1 text-xs font-mono ${
              connected ? "bg-hud-green/20 text-hud-green" : "bg-hud-red/20 text-hud-red"
            }`}
          >
            {connected ? "Connected" : "Disconnected"}
          </span>
        </div>
        <p className="mt-1 text-sm text-hud-dim">Mission Control</p>
      </header>

      <main className="p-6">
        <section className="mb-6">
          <LiveGraph metrics={metrics} />
        </section>
        <section className="rounded border border-hud-border bg-hud-panel/80 p-4">
          <div className="mb-2 text-xs text-hud-dim">PROCESSES (top by CPU)</div>
          <div className="font-mono text-sm">
            {metrics.length === 0 && (
              <div className="text-hud-dim">Waiting for metrics...</div>
            )}
            {metrics
              .slice()
              .sort((a, b) => (b.cpu_percent ?? 0) - (a.cpu_percent ?? 0))
              .slice(0, 12)
              .map((m) => (
                <div
                  key={`${m.pid}-${m.name}`}
                  className="flex justify-between border-b border-hud-border/50 py-1 last:border-0"
                >
                  <span className="text-hud-green">{m.name}</span>
                  <span className="text-hud-dim">
                    PID {m.pid} | CPU {m.cpu_percent?.toFixed(1)}% | {m.mem_mb?.toFixed(0)} MB
                  </span>
                </div>
              ))}
          </div>
        </section>
      </main>

      {anomaly && (
        <FlashAlert
          payload={anomaly}
          onApplyFix={sendApplyFix}
          onDismiss={clearAnomaly}
        />
      )}
    </div>
  );
}

export default App;
