"use client";
import { useEffect, useState } from "react";
import { get } from "@/components/api";

type Health = {
  live: Record<string, any> | null;
  today: string; runs: { kind: string; session_date: string | null; started_at: string; finished_at: string | null; status: string | null; error: string | null }[];
  failures_7d: { kind: string; started_at: string; error: string }[];
  freshness: Record<string, string | null>; surveillance: Record<string, any>;
  watchlist_sizes: { session: string; listed: number }[];
};
const t = (s: string | null) => (s ? new Date(s).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" }) : "–");

export default function Ops() {
  const [h, setH] = useState<Health | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { get<Health>("/api/health").then(setH).catch((e) => setErr(String(e))); }, []);
  if (err) return <p className="muted">{err}</p>;
  if (!h) return <p className="muted">Loading…</p>;
  return (
    <>
      <h2>Ops health <span className="muted" style={{ fontSize: 14 }}>({h.today})</span></h2>
      <p className="muted">Freshness, run status and errors only. No outcomes (blind until the look).</p>
      <h3>Live WebSocket (LIVE VIEW — not used by drishti-swing-v1)</h3>
      {!h.live ? <p className="muted">The recorder has not run yet.</p> : (
        <div className="row" style={{ marginBottom: 12 }}>
          <div className="card">state <b className={h.live.state === "connected" && Date.now() - new Date(h.live.updated_at).getTime() < 30000 ? "" : "muted"}>{h.live.state}</b>{Date.now() - new Date(h.live.updated_at).getTime() > 30000 && h.live.state !== "stopped" ? " (stale)" : ""}</div>
          <div className="card">keys {h.live.keys} · connections {h.live.connections} · reconnects {h.live.reconnects}</div>
          <div className="card">frames {h.live.frames?.toLocaleString("en-IN")} · last data {t(h.live.last_data_at)}</div>
          <div className="card">ticks {h.live.tick_writing ? "writing" : "STOPPED"} · {h.live.tick_files} files · raw {(h.live.tick_bytes_raw / 1e6).toFixed(1)} MB{h.live.tick_gb_today != null ? ` · on disk ${h.live.tick_gb_today} GB` : ""} · disk free {h.live.disk_free_gb} GB</div>
          <div className="card">1m bars written {h.live.bars_rows}</div>
          {h.live.alerts?.length > 0 && <div className="card" style={{ color: "var(--down)" }}>alerts: {h.live.alerts.map((a: any) => a.msg).join("; ")}</div>}
        </div>)}
      <div className="row" style={{ marginBottom: 12 }}>
        {Object.entries(h.freshness).map(([k, v]) => <div className="card" key={k}>{k.replace(/_/g, " ")}: <b>{v ?? "–"}</b></div>)}
      </div>
      <div className="scroll"><table>
        <thead><tr><th>Job</th><th>Session</th><th>Started</th><th>Finished</th><th>Status</th><th>Error</th></tr></thead>
        <tbody>{h.runs.map((r) => (
          <tr key={r.kind}><td>{r.kind}</td><td>{r.session_date ?? "–"}</td><td>{t(r.started_at)}</td><td>{t(r.finished_at)}</td>
            <td><span className={`pill ${r.status === "OK" ? "on" : r.status === "NOOP" ? "" : "off"}`}>{r.status ?? "running"}</span></td>
            <td className="muted">{r.error ?? ""}</td></tr>))}</tbody>
      </table></div>
      <h3>ASM / GSM files</h3>
      <pre className="card">{JSON.stringify(h.surveillance, null, 1)}</pre>
      <h3>Failures (7 days)</h3>
      {h.failures_7d.length === 0 ? <p className="muted">None.</p> :
        <ul>{h.failures_7d.map((f, i) => <li key={i}>{t(f.started_at)} {f.kind}: {f.error}</li>)}</ul>}
      <h3>Watchlist sizes</h3>
      <p>{h.watchlist_sizes.map((w) => `${w.session}: ${w.listed}`).join(" · ") || "–"}</p>
    </>
  );
}
