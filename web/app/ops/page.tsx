"use client";
import { useEffect, useState } from "react";
import { get } from "@/components/api";

type Health = {
  live: Record<string, any> | null;
  today: string;
  runs: {
    kind: string;
    session_date: string | null;
    started_at: string;
    finished_at: string | null;
    status: string | null;
    error: string | null;
  }[];
  failures_7d: { kind: string; started_at: string; error: string }[];
  freshness: Record<string, string | null>;
  surveillance: Record<string, any>;
  watchlist_sizes: { session: string; listed: number }[];
};

const t = (s: string | null) => (s ? new Date(s).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" }) : "–");
const RUN_STALE_H = 8;

function runPill(r: { status: string | null; started_at: string }): [string, string, boolean] {
  if (r.status === null) {
    const stale = Date.now() - new Date(r.started_at).getTime() > RUN_STALE_H * 3600e3;
    return stale ? ["off", "stale (no end)", false] : ["on", "running", true];
  }
  return [r.status === "OK" ? "on" : r.status === "NOOP" ? "" : "off", r.status, false];
}

export default function Ops() {
  const [h, setH] = useState<Health | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    get<Health>("/api/health")
      .then(setH)
      .catch((e) => setErr(String(e)));
  }, []);

  if (err) return <p className="muted" style={{ padding: 20 }}>{err}</p>;
  if (!h) return <p className="muted" style={{ padding: 20 }}>Loading system health…</p>;

  return (
    <div style={{ animation: "pageFadeIn 0.3s ease-out" }}>
      <div className="row" style={{ marginBottom: 16, justifyContent: "space-between" }}>
        <h2 style={{ margin: 0, fontSize: 22, fontWeight: 800 }}>
          Ops Health Radar <span className="muted" style={{ fontSize: 14, fontWeight: 500 }}>({h.today})</span>
        </h2>
        <span className="muted small">Freshness, run status and errors only. No outcomes (blind until the look).</span>
      </div>

      <h3 style={{ fontSize: 16, marginBottom: 10 }}>Live WebSocket Feed (LIVE VIEW)</h3>
      {!h.live ? (
        <div className="card muted" style={{ marginBottom: 16 }}>The recorder has not run yet today.</div>
      ) : (
        <div className="ops-grid">
          {(() => {
            const old = Date.now() - new Date(h.live.updated_at).getTime() > 30000;
            const bad = h.live.state === "error" || (old && h.live.state !== "stopped");
            const cls = bad ? "off" : h.live.state === "connected" ? "on" : "";
            return (
              <div className="ops-card">
                <div className="muted small">RECORDER STATE</div>
                <div style={{ marginTop: 4 }}>
                  <span className={`pill ${cls}`}>
                    {h.live.state === "connected" && <span className="live-dot" />}
                    {h.live.state}{bad && old ? " (stale)" : ""}
                  </span>
                </div>
              </div>
            );
          })()}
          <div className="ops-card">
            <div className="muted small">CONNECTIONS & KEYS</div>
            <b>{h.live.keys}</b> keys · <b>{h.live.connections}</b> conn · {h.live.reconnects} reconnects
          </div>
          <div className="ops-card">
            <div className="muted small">FRAME THROUGHPUT</div>
            <b>{h.live.frames?.toLocaleString("en-IN")}</b> frames · last {t(h.live.last_data_at)}
          </div>
          <div className="ops-card">
            <div className="muted small">STORAGE & TICKS</div>
            <span style={{ color: h.live.tick_writing ? "var(--up)" : "var(--warn)", fontWeight: 700 }}>
              {h.live.tick_writing ? "WRITING" : "STOPPED"}
            </span> · {h.live.tick_files} files · {(h.live.tick_bytes_raw / 1e6).toFixed(1)} MB
          </div>
          <div className="ops-card">
            <div className="muted small">1M BARS COMMITTED</div>
            <b>{h.live.bars_rows}</b> rows · disk free <b>{h.live.disk_free_gb} GB</b>
          </div>
        </div>
      )}

      {h.live && h.live.alerts && h.live.alerts.length > 0 && (
        <div className="card" style={{ color: "var(--down)", marginBottom: 16, borderColor: "var(--down-border)", background: "var(--down-bg)" }}>
          <b>Active Alerts:</b> {h.live.alerts.map((a: any) => a.msg).join("; ")}
        </div>
      )}

      <h3 style={{ fontSize: 16, marginBottom: 10 }}>Data Pipeline Freshness</h3>
      <div className="ops-grid">
        {Object.entries(h.freshness).map(([k, v]) => (
          <div className="ops-card" key={k}>
            <div className="muted small">{k.replace(/_/g, " ").toUpperCase()}</div>
            <b style={{ fontSize: 15 }}>{v ?? "–"}</b>
          </div>
        ))}
      </div>

      <h3 style={{ fontSize: 16, marginBottom: 10 }}>Job Execution History</h3>
      <div className="scroll" style={{ marginBottom: 20 }}>
        <table>
          <thead>
            <tr>
              <th>Job Kind</th>
              <th>Session</th>
              <th>Started</th>
              <th>Finished</th>
              <th>Status</th>
              <th>Error Details</th>
            </tr>
          </thead>
          <tbody>
            {h.runs.map((r) => {
              const [cls, label, isRunning] = runPill(r);
              return (
                <tr key={r.kind}>
                  <td><b>{r.kind}</b></td>
                  <td>{r.session_date ?? "–"}</td>
                  <td>{t(r.started_at)}</td>
                  <td>{t(r.finished_at)}</td>
                  <td>
                    <span className={`pill ${cls}`}>
                      {isRunning && <span className="live-dot" />}
                      {label}
                    </span>
                  </td>
                  <td className="muted small">{r.error ?? ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <h3 style={{ fontSize: 16, marginBottom: 10 }}>ASM / GSM Surveillance Cache</h3>
      <pre className="terminal">{JSON.stringify(h.surveillance, null, 2)}</pre>

      <h3 style={{ fontSize: 16, marginBottom: 10 }}>Pipeline Failures (Past 7 Days)</h3>
      {h.failures_7d.length === 0 ? (
        <p className="muted small">None recorded (all runs successful).</p>
      ) : (
        <ul style={{ paddingLeft: 18 }}>
          {h.failures_7d.map((f, i) => (
            <li key={i} style={{ marginBottom: 4 }}>
              <span className="muted">{t(f.started_at)}</span> <b>{f.kind}</b>: <span style={{ color: "var(--down)" }}>{f.error}</span>
            </li>
          ))}
        </ul>
      )}

      <h3 style={{ fontSize: 16, marginBottom: 10 }}>Historical Watchlist Sizes</h3>
      <div className="card" style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
        {h.watchlist_sizes.map((w) => (
          <span key={w.session} className="pill" style={{ background: "transparent" }}>
            <span className="muted">{w.session}:</span> <b>{w.listed}</b>
          </span>
        ))}
      </div>
    </div>
  );
}
