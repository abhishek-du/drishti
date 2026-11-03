"use client";
import { useEffect, useState } from "react";
import { fmt, get, stockHref } from "@/components/api";
import { LIVE_LABEL, LiveMsg, openLive, stale } from "@/components/live";

type Row = {
  instrument_key: string;
  symbol: string | null;
  event: string;
  rank: number;
  alpha_rank?: number;
  rs_score: number;
  metrics: Record<string, number>;
  flags: { candles: string[]; results_meeting: string | null };
  alpha_tags?: string[];
  is_strict_alpha?: boolean;
  is_relaxed_alpha?: boolean;
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
  alpha_rows?: Row[];
  alpha_counts?: {
    total: number;
    vol_surge: number;
    no_earnings: number;
    trigger_ready: number;
    strict_alpha: number;
    selected_alpha: number;
  };
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
  const [tab, setTab] = useState<"alpha" | "all">("alpha");

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
  const alphaList = wl.alpha_rows && wl.alpha_rows.length > 0 ? wl.alpha_rows : [];
  const displayedRows = tab === "alpha" ? (alphaList.length > 0 ? alphaList : wl.rows) : wl.rows;

  return (
    <div style={{ animation: "pageFadeIn 0.3s ease-out" }}>
      {/* Header bar */}
      <div className="row" style={{ marginBottom: 14, justifyContent: "space-between" }}>
        <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
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

      {/* Laser-Focus Tab Navigation */}
      <div className="row" style={{ gap: 10, marginTop: 14, marginBottom: 12 }}>
        <button
          type="button"
          className={tab === "alpha" ? "sel" : ""}
          onClick={() => setTab("alpha")}
          style={{
            padding: "8px 16px",
            borderRadius: 8,
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontSize: 13,
            borderWidth: tab === "alpha" ? 2 : 1,
            background: tab === "alpha" ? "rgba(59, 130, 246, 0.12)" : undefined,
          }}
        >
          <span style={{ fontSize: 15 }}>🎯</span>
          <b>Laser-Focus Alpha</b>
          <span className="pill on" style={{ fontSize: 11, padding: "1px 7px" }}>
            {alphaList.length} Picks
          </span>
        </button>

        <button
          type="button"
          className={tab === "all" ? "sel" : ""}
          onClick={() => setTab("all")}
          style={{
            padding: "8px 16px",
            borderRadius: 8,
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontSize: 13,
            borderWidth: tab === "all" ? 2 : 1,
          }}
        >
          <span style={{ fontSize: 15 }}>📊</span>
          <span>Broad Watchlist Radar</span>
          <span className="pill" style={{ fontSize: 11, padding: "1px 7px" }}>
            {wl.rows.length} Stocks
          </span>
        </button>
      </div>

      {/* Alpha Laser Funnel Banner */}
      {tab === "alpha" && (
        <div className="card" style={{ marginBottom: 14, background: "rgba(59, 130, 246, 0.05)", borderColor: "rgba(59, 130, 246, 0.25)" }}>
          <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
            <div>
              <div className="row" style={{ gap: 8, alignItems: "center" }}>
                <b style={{ color: "var(--brand)", fontSize: 13, textTransform: "uppercase", letterSpacing: "0.5px" }}>
                  🎯 Drishti Advanced Alpha Funnel (Steps 10–12)
                </b>
                <span className="pill on" style={{ fontSize: 11 }}>Execution Focus</span>
              </div>
              <div className="muted small" style={{ marginTop: 4 }}>
                Filtered from <b>{wl.alpha_counts?.total ?? wl.rows.length}</b> candidates down to top <b>{alphaList.length}</b> ready-to-move leaders. Zero earnings risk · Smart Money volume surge · Immediate breakout trigger.
              </div>
            </div>
            <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
              <span className="pill" title="Step 10: Volume >= 1.5x 20d avg or >= 1.25x 50d avg">
                ⚡ Vol Surge ({wl.alpha_counts?.vol_surge ?? "–"})
              </span>
              <span className="pill" title="Step 11: Zero Event Risk (No earnings meeting in next 6 sessions)">
                🛡️ Event Safe ({wl.alpha_counts?.no_earnings ?? "–"})
              </span>
              <span className="pill" title="Step 12: Price within 3% of 52W high or 5-day breakout">
                🚀 Ready Trigger ({wl.alpha_counts?.trigger_ready ?? "–"})
              </span>
            </div>
          </div>
        </div>
      )}

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
              {tab === "alpha" && <th>Alpha Catalysts</th>}
              <th>Event</th>
              <th className="num">RS</th>
              <th className="num">Close</th>
              <th className="num" title="ATR as % of close">ATR %</th>
              <th className="num" title="Distance from the 52-week high">52w hi</th>
              <th className="num">RSI</th>
              <th className="num" title="Volume Surge relative to 20d or 50d average">Vol Surge</th>
              <th>Candle (T)</th>
              <th>Results</th>
              <th className="num" title={LIVE_LABEL}>
                Live LTP · %<br /><span className="muted" style={{ fontWeight: 400 }}>VWAP</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {displayedRows.map((r, i) => {
              const volRatio = r.metrics.volr20 ?? r.metrics.volr50 ?? 0;
              const isVolHot = volRatio >= 1.5 || (r.metrics.volr50 ?? 0) >= 1.25;
              const isNearHigh = (r.metrics.dist_52w_high_pct ?? 999) <= 3.0;

              return (
                <tr key={r.instrument_key} style={tab === "alpha" ? { background: i % 2 === 0 ? "rgba(59, 130, 246, 0.02)" : undefined } : undefined}>
                  <td className="num">
                    {tab === "alpha" ? (
                      <div>
                        <b style={{ color: "var(--brand)", fontSize: 14 }}>#{r.alpha_rank ?? (i + 1)}</b>
                        <div className="muted" style={{ fontSize: 10 }}>RS #{r.rank}</div>
                      </div>
                    ) : (
                      r.rank
                    )}
                  </td>
                  <td>
                    <div className="row" style={{ gap: 6, alignItems: "center" }}>
                      <a href={stockHref(r.instrument_key)} title="Open Interactive Chart" style={{ fontWeight: 700 }}>
                        {r.symbol ?? r.instrument_key}
                      </a>
                      {tab === "all" && r.is_strict_alpha && (
                        <span className="pill on" style={{ fontSize: 10, padding: "0px 5px" }} title="Qualifies for Laser-Focus Alpha">
                          ALPHA
                        </span>
                      )}
                    </div>
                  </td>
                  {tab === "alpha" && (
                    <td>
                      <div className="row" style={{ gap: 4, flexWrap: "wrap" }}>
                        {isVolHot && (
                          <span className="pill on" style={{ fontSize: 10, padding: "1px 5px" }} title="Institutional Volume Surge">
                            ⚡ {fmt(volRatio, 1)}x
                          </span>
                        )}
                        {isNearHigh && (
                          <span className="pill on" style={{ fontSize: 10, padding: "1px 5px" }} title="Within 3% of 52W High">
                            🚀 {fmt(r.metrics.dist_52w_high_pct, 1)}%
                          </span>
                        )}
                        {!r.flags.results_meeting && (
                          <span className="pill" style={{ fontSize: 10, padding: "1px 5px" }} title="No upcoming earnings meeting">
                            🛡️ Safe
                          </span>
                        )}
                      </div>
                    </td>
                  )}
                  <td>
                    <span className={`pill ${r.event === "ADDED" ? "on" : ""}`} style={{ fontSize: 11, padding: "1px 6px" }}>
                      {r.event}
                    </span>
                  </td>
                  <td className="num">{fmt(r.rs_score)}</td>
                  <td className="num">{fmt(r.metrics.close)}</td>
                  <td className="num">{fmt(100 * r.metrics.atr_pct, 1)}%</td>
                  <td className="num" style={{ color: isNearHigh ? "var(--up)" : undefined, fontWeight: isNearHigh ? 700 : 400 }}>
                    {fmt(-r.metrics.dist_52w_high_pct, 1)}%
                  </td>
                  <td className="num">{fmt(r.metrics.rsi, 0)}</td>
                  <td className="num" style={{ color: isVolHot ? "var(--up)" : undefined, fontWeight: isVolHot ? 700 : 400 }}>
                    {fmt(volRatio, 1)}x
                  </td>
                  <td className="clip" title={r.flags.candles.join(", ")}>
                    {r.flags.candles.join(", ") || <span className="muted">–</span>}
                  </td>
                  <td>
                    {r.flags.results_meeting ? (
                      <span className="pill warn" style={{ fontSize: 11 }}>{r.flags.results_meeting}</span>
                    ) : (
                      <span className="muted">Safe</span>
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
              );
            })}
          </tbody>
        </table>
      </div>
      {wl.dropped.length > 0 && (
        <p className="muted small" style={{ marginTop: 8 }}>Dropped since the last run: {wl.dropped.length}</p>
      )}
    </div>
  );
}
