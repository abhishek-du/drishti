"use client";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import Chart, { ChartData } from "@/components/Chart";
import LiveChart from "@/components/LiveChart";
import { fmt, get } from "@/components/api";

export default function Stock() {
  const { key } = useParams<{ key: string }>();
  const k = decodeURIComponent(key);
  const [d, setD] = useState<ChartData | null>(null);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState<"daily" | "live">("daily");
  useEffect(() => { if (new URLSearchParams(location.search).get("tab") === "live") setTab("live"); }, []);
  useEffect(() => { get<ChartData>(`/api/chart?key=${encodeURIComponent(k)}`).then(setD).catch((e) => setErr(String(e))); }, [k]);
  if (err) return <p className="muted">{err}</p>;
  if (!d) return <p className="muted">Loading…</p>;
  const last = d.bars[d.bars.length - 1];
  return (
    <>
      <div className="row" style={{ marginBottom: 8 }}>
        <h2 style={{ margin: 0 }}>{d.symbol}</h2>
        <span className="muted">{d.name}{d.sector ? ` · ${d.sector}` : ""}</span>
        <span className={`pill ${d.last_bar_source === "official" ? "on" : "warn"}`}>last bar {last.time}: {d.last_bar_source}</span>
        <span className="card">close {fmt(last.close)} · ATR {fmt(d.last.atr)} ({fmt(100 * (d.last.atr_pct ?? 0), 1)}%) · RSI {fmt(d.last.rsi, 0)}</span>
      </div>
      <div className="row" style={{ marginBottom: 8 }}>
        <button onClick={() => setTab("daily")} style={{ fontWeight: tab === "daily" ? 700 : 400 }}>Daily</button>
        <button onClick={() => setTab("live")} style={{ fontWeight: tab === "live" ? 700 : 400 }}>Live intraday</button>
      </div>
      {tab === "daily" ? (
        <>
          <Chart data={d} />
          <p className="muted">Daily bars, split/bonus adjusted. Zones are as known on the last bar (spec §3). Signals arrive with P3.</p>
        </>
      ) : <LiveChart instrumentKey={k} />}
    </>
  );
}
