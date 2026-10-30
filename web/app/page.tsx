"use client";
import { useEffect, useState } from "react";
import { fmt, get, stockHref } from "@/components/api";
import { LIVE_LABEL, LiveMsg, openLive, stale } from "@/components/live";

type Row = {
  instrument_key: string;
  symbol: string | null;
  event: string;
  rank: number;
  rs_score: number;
  metrics: Record<string, number>;
  flags: { candles: string[]; results_meeting: string | null };
};

type WL = {
  run: {
    session_date: string;
    version: string;
    bar_source: string;
    created_at: string;
    regime: Record<string, any>;
    surveillance: Record<string, any>;
    counts: Record<string, number>;
  };
  rows: Row[];
  dropped: string[];
};

const FUNNEL = [
  "with_bar_on_T", "eq_series", "eligible", "band_ok", "trend_up",
  "rs_top", "beats_nifty500_3m", "near_52w_high", "atr_pct_ok", "listed"
];

export default function Watchlist() {
  const [dates, setDates] = useState<string[]>([]);
  const [date, setDate] = useState<string>("");
  const [wl, setWl] = useState<WL | null>(null);
  const [err, setErr] = useState("");
  const [live, setLive] = useState<LiveMsg | null>(null);

  useEffect(() => openLive(setLive), []);
  useEffect(() => {
    get<string[]>("/api/watchlist/dates")
      .then((d) => { setDates(d); setDate(d[0] ?? ""); })
      .catch((e) => setErr(String(e)));
  }, []);
  useEffect(() => {
    if (date) get<WL>(`/api/watchlist?date=${date}`).then(setWl).catch((e) => setErr(String(e)));
  }, [date]);

  if (err) return <p className="muted" style={{ padding: 20 }}>{err}</p>;
  if (!wl) return <p className="muted" style={{ padding: 20 }}>Loading watchlist radar…</p>;

  const g = wl.run.regime;
  const c = wl.run.counts;
  const isLiveActive = !stale(live?.summary.updated_at);

  return (
    <div style={{ animation: "pageFadeIn 0.3s ease-out" }}>
      {/* Header bar */}
      <div className="row" style={{ marginBottom: 14, justifyContent: "space-between" }}>
        <div className="row" style={{ gap: 10 }}>
          <h2 style={{ margin: 0, fontSize: 22, fontWeight: 800 }}>Watchlist Radar</h2>
          <select value={date} onChange={(e) => setDate(e.target.value)} aria-label="Session">
            {dates.map((d) => <option key={d}>{d}</option>)}
          </select>
          <span className="pill">{wl.run.version}</span>
          <span className={`pill ${wl.run.bar_source === "official" ? "on" : "warn"}`}>
            bar: {wl.run.bar_source}
          </span>
          <span className={`pill ${g.status === "ON" ? "on" : "off"}`}>
            regime {g.status}
          </span>
          {g.spec_gate && (
            <span className={`pill ${g.spec_gate === "ON" ? "on" : "off"}`} title="spec §5 gate: Nifty 500 > SMA200; OFF = no new S1a–S3 entries (SC4)">
              spec gate {g.spec_gate}
            </span>
          )}
          {g.stale && <span className="pill warn">index bar from {g.nifty500_date}</span>}
          {g.vix_spike && <span className="pill off">VIX spike</span>}
          {wl.run.surveillance?.warning && <span className="pill warn">ASM/GSM file stale</span>}
        </div>

        <div className="row" style={{ gap: 8 }}>
          <span className={`pill ${isLiveActive ? "on" : ""}`} title={LIVE_LABEL}>
            {isLiveActive && <span className="live-dot" />}
            {LIVE_LABEL}: {isLiveActive ? "live feed" : "not running"}
          </span>
        </div>
      </div>

      {/* Regime & Summary Cards */}
      <div className="row" style={{ marginBottom: 14 }}>
        <div className="card" style={{ flex: 1, minWidth: 260 }}>
          <div className="muted small" style={{ marginBottom: 2 }}>INDEX REGIME (NIFTY 500)</div>
          <b>{fmt(g.nifty500)}</b> · SMA200 {fmt(g.nifty500_sma200)} · 3M <span style={{ color: g.nifty500_ret63 >= 0 ? "var(--up)" : "var(--down)" }}>{fmt(100 * g.nifty500_ret63, 1)}%</span>
        </div>
        <div className="card" style={{ flex: 1, minWidth: 220 }}>
          <div className="muted small" style={{ marginBottom: 2 }}>VOLATILITY RADAR</div>
          India VIX <b>{fmt(g.vix)}</b> · 5d change <span style={{ color: g.vix_5d_change <= 0 ? "var(--up)" : "var(--down)" }}>{fmt(100 * g.vix_5d_change, 1)}%</span>
        </div>
        <div className="card" style={{ flex: 1.5, minWidth: 280 }}>
          <div className="muted small" style={{ marginBottom: 2 }}>CANDIDATE FUNNEL</div>
          <div className="small" style={{ color: "var(--fg-secondary)" }}>
            added <b style={{ color: "var(--up)" }}>{c.added}</b> · kept <b>{c.kept}</b> · dropped <b style={{ color: "var(--down)" }}>{c.dropped}</b> · results ahead <b>{c.results_ahead}</b>
          </div>
        </div>
      </div>

      <div className="card muted small" style={{ marginBottom: 14, overflowX: "auto", whiteSpace: "nowrap" }}>
        {FUNNEL.map((k) => `${k.replace(/_/g, " ")} (${c[k] ?? "–"})`).join(" → ")}
      </div>

      <p className="muted small" style={{ marginTop: 0, marginBottom: 10 }}>
        A watchlist is context, not a signal. Outcomes are hidden until the look (blind rule).
      </p>

      {/* Main Watchlist Table */}
      <div className="scroll wl">
        <table>
          <thead>
            <tr>
              <th className="num">#</th>
              <th>Symbol</th>
              <th>Event</th>
              <th className="num">RS</th>
              <th className="num">Close</th>
              <th className="num" title="ATR as % of close">ATR %</th>
              <th className="num" title="Distance from the 52-week high">52w hi</th>
              <th className="num">RSI</th>
              <th className="num" title="Volume / 50-day average volume">Vol/50d</th>
              <th>Candle (T)</th>
              <th>Results</th>
              <th className="num" title={LIVE_LABEL}>
                Live LTP · %<br /><span className="muted" style={{ fontWeight: 400 }}>VWAP</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {wl.rows.map((r) => (
              <tr key={r.instrument_key}>
                <td className="num">{r.rank}</td>
                <td>
                  <a href={stockHref(r.instrument_key)} title="Open Interactive Chart">
                    {r.symbol ?? r.instrument_key}
                  </a>
                </td>
                <td>
                  <span className={`pill ${r.event === "ADDED" ? "on" : ""}`} style={{ fontSize: 11, padding: "1px 6px" }}>
                    {r.event}
                  </span>
                </td>
                <td className="num">{fmt(r.rs_score)}</td>
                <td className="num">{fmt(r.metrics.close)}</td>
                <td className="num">{fmt(100 * r.metrics.atr_pct, 1)}%</td>
                <td className="num" style={{ color: r.metrics.dist_52w_high_pct > -0.05 ? "var(--up)" : undefined }}>
                  {fmt(-r.metrics.dist_52w_high_pct, 1)}%
                </td>
                <td className="num">{fmt(r.metrics.rsi, 0)}</td>
                <td className="num">{fmt(r.metrics.volr50)}</td>
                <td className="clip" title={r.flags.candles.join(", ")}>
                  {r.flags.candles.join(", ") || <span className="muted">–</span>}
                </td>
                <td>
                  {r.flags.results_meeting ? (
                    <span className="pill warn" style={{ fontSize: 11 }}>{r.flags.results_meeting}</span>
                  ) : (
                    <span className="muted">–</span>
                  )}
                </td>
                {(() => {
                  const q = live?.summary.keys[r.instrument_key];
                  return (
                    <td className="num" title={LIVE_LABEL}>
                      <b>{fmt(q?.ltp)}</b>{" "}
                      <span style={{ color: (q?.chg_pct ?? 0) >= 0 ? "var(--up)" : "var(--down)", fontWeight: 600 }}>
                        {q?.chg_pct == null ? "" : `${q.chg_pct >= 0 ? "+" : ""}${fmt(q.chg_pct)}%`}
                      </span>
                      <br />
                      <span className="muted" style={{ fontSize: 11 }}>{q?.vwap == null ? "–" : `VWAP ${fmt(q.vwap)}`}</span>
                    </td>
                  );
                })()}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {wl.dropped.length > 0 && (
        <p className="muted small" style={{ marginTop: 8 }}>Dropped since the last run: {wl.dropped.length}</p>
      )}
    </div>
  );
}
