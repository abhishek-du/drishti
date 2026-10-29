"use client";
import { useEffect, useState } from "react";
import { fmt, get, stockHref } from "@/components/api";
import { LIVE_LABEL, LiveMsg, openLive, stale } from "@/components/live";

type Row = {
  instrument_key: string; symbol: string | null; event: string; rank: number; rs_score: number;
  metrics: Record<string, number>; flags: { candles: string[]; results_meeting: string | null };
};
type WL = {
  run: { session_date: string; version: string; bar_source: string; created_at: string;
         regime: Record<string, any>; surveillance: Record<string, any>; counts: Record<string, number> };
  rows: Row[]; dropped: string[];
};
const FUNNEL = ["with_bar_on_T", "eq_series", "eligible", "band_ok", "trend_up", "rs_top", "beats_nifty500_3m",
  "near_52w_high", "atr_pct_ok", "listed"];

export default function Watchlist() {
  const [dates, setDates] = useState<string[]>([]);
  const [date, setDate] = useState<string>("");
  const [wl, setWl] = useState<WL | null>(null);
  const [err, setErr] = useState("");
  const [live, setLive] = useState<LiveMsg | null>(null);
  useEffect(() => openLive(setLive), []);
  useEffect(() => { get<string[]>("/api/watchlist/dates").then((d) => { setDates(d); setDate(d[0] ?? ""); }).catch((e) => setErr(String(e))); }, []);
  useEffect(() => { if (date) get<WL>(`/api/watchlist?date=${date}`).then(setWl).catch((e) => setErr(String(e))); }, [date]);
  if (err) return <p className="muted">{err}</p>;
  if (!wl) return <p className="muted">Loading…</p>;
  const g = wl.run.regime, c = wl.run.counts;
  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>Watchlist</h2>
        <select value={date} onChange={(e) => setDate(e.target.value)} aria-label="Session">
          {dates.map((d) => <option key={d}>{d}</option>)}
        </select>
        <span className="pill">{wl.run.version}</span>
        <span className={`pill ${wl.run.bar_source === "official" ? "on" : "warn"}`}>bar: {wl.run.bar_source}</span>
        <span className={`pill ${g.status === "ON" ? "on" : "off"}`}>regime {g.status}</span>
        {g.spec_gate && <span className={`pill ${g.spec_gate === "ON" ? "on" : "off"}`} title="spec §5 gate: Nifty 500 > SMA200; OFF = no new S1a–S3 entries (SC4)">spec gate {g.spec_gate}</span>}
        {g.stale && <span className="pill warn">index bar from {g.nifty500_date}</span>}
        {g.vix_spike && <span className="pill off">VIX spike</span>}
        {wl.run.surveillance?.warning && <span className="pill warn">ASM/GSM file stale</span>}
      </div>
      <div className="row" style={{ marginBottom: 12 }}>
        <div className="card">Nifty 500 {fmt(g.nifty500)} · SMA200 {fmt(g.nifty500_sma200)} · 3M {fmt(100 * g.nifty500_ret63, 1)}%</div>
        <div className="card">India VIX {fmt(g.vix)} · 5d {fmt(100 * g.vix_5d_change, 1)}%</div>
        <div className="card muted">{FUNNEL.map((k) => `${k.replace(/_/g, " ")} ${c[k] ?? "–"}`).join(" → ")}</div>
        <div className="card">added {c.added} · kept {c.kept} · dropped {c.dropped} · results ahead {c.results_ahead}</div>
      </div>
      <p className="muted" style={{ marginTop: 0 }}>A watchlist is context, not a signal. Outcomes are hidden until the look (blind rule).
        {" "}<span className={`pill ${stale(live?.summary.updated_at) ? "" : "on"}`}>{LIVE_LABEL}: {stale(live?.summary.updated_at) ? "not running" : "live"}</span></p>
      <div className="scroll">
        <table>
          <thead><tr>
            <th className="num">#</th><th>Symbol</th><th>Event</th><th className="num">RS</th><th className="num">Close</th>
            <th className="num">ATR %</th><th className="num">From 52w high</th><th className="num">RSI</th>
            <th className="num">Vol/50d</th><th>Candle (T)</th><th>Results</th>
            <th className="num" title={LIVE_LABEL}>Live LTP</th><th className="num" title={LIVE_LABEL}>Live %</th><th className="num" title={LIVE_LABEL}>VWAP</th>
          </tr></thead>
          <tbody>
            {wl.rows.map((r) => (
              <tr key={r.instrument_key}>
                <td className="num">{r.rank}</td>
                <td><a href={stockHref(r.instrument_key)}>{r.symbol ?? r.instrument_key}</a></td>
                <td><span className={`pill ${r.event === "ADDED" ? "on" : ""}`}>{r.event}</span></td>
                <td className="num">{fmt(r.rs_score)}</td>
                <td className="num">{fmt(r.metrics.close)}</td>
                <td className="num">{fmt(100 * r.metrics.atr_pct, 1)}</td>
                <td className="num">{fmt(-r.metrics.dist_52w_high_pct, 1)}%</td>
                <td className="num">{fmt(r.metrics.rsi, 0)}</td>
                <td className="num">{fmt(r.metrics.volr50)}</td>
                <td>{r.flags.candles.join(", ") || <span className="muted">–</span>}</td>
                <td>{r.flags.results_meeting ? <span className="pill warn">{r.flags.results_meeting}</span> : <span className="muted">–</span>}</td>
                {(() => { const q = live?.summary.keys[r.instrument_key]; return (<>
                  <td className="num">{fmt(q?.ltp)}</td>
                  <td className="num" style={{ color: (q?.chg_pct ?? 0) >= 0 ? "var(--up)" : "var(--down)" }}>{q?.chg_pct == null ? "–" : fmt(q.chg_pct)}</td>
                  <td className="num">{fmt(q?.vwap)}</td></>); })()}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {wl.dropped.length > 0 && <p className="muted">Dropped since the last run: {wl.dropped.length}</p>}
    </>
  );
}
