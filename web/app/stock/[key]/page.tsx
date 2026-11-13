"use client";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import type { BarsData, Tf } from "@/components/kchart/KChart";
import { fmt, get } from "@/components/api";
import { LIVE_LABEL } from "@/components/live";
import AiAnalysisCard from "@/components/AiAnalysisCard";
import AnalysisPanel from "@/components/AnalysisPanel";
import VerdictBar from "@/components/VerdictBar";

// klinecharts touches `window` when imported, so the chart loads in the browser only (no SSR)
const KChart = dynamic(() => import("@/components/kchart/KChart"), {
  ssr: false,
  loading: () => (
    <div className="card" style={{ height: 480, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <p className="muted">Loading chart engine…</p>
    </div>
  ),
});

const TFS: Tf[] = ["1m", "5m", "15m", "1h", "1d"];
const isLive = (tf: Tf) => tf === "1m" || tf === "5m";

type Trends = { trends: Record<Tf, "UP" | "DOWN" | "SIDE" | null> };
const TREND_CLASS = { UP: "on", DOWN: "off", SIDE: "" } as const;

function tfFromUrl(): Tf {
  const q = new URLSearchParams(location.search);
  const tf = q.get("tf") as Tf | null;
  if (tf && TFS.includes(tf)) return tf;
  return q.get("tab") === "live" ? "1m" : "1d";
}

export default function Stock() {
  const { key } = useParams<{ key: string }>();
  const k = decodeURIComponent(key);
  const [tf, setTfRaw] = useState<Tf | null>(null);
  const [d, setD] = useState<BarsData | null>(null);
  const [trends, setTrends] = useState<Trends["trends"] | null>(null);
  const [showSide, setShowSide] = useState(true);

  useEffect(() => setTfRaw(tfFromUrl()), []);
  const setTf = (t: Tf) => {
    setTfRaw(t);
    const u = new URL(location.href);
    u.searchParams.set("tf", t);
    u.searchParams.delete("tab");
    history.replaceState(null, "", u);
  };
  const onData = useCallback((x: BarsData | null) => setD(x), []);

  useEffect(() => {
    get<Trends>(`/api/chart2/trends?key=${encodeURIComponent(k)}`)
      .then((x) => setTrends(x.trends))
      .catch(() => setTrends(null));
  }, [k]);

  return (
    <div className="stockpage">
      {/* Sleek Compact Header Bar (Height ~44px, Single Row) */}
      <div
        className="row stock-header-bar"
        style={{
          marginBottom: 10,
          padding: "8px 14px",
          background: "var(--card)",
          borderRadius: 8,
          border: "1px solid var(--card-border)",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 10,
        }}
      >
        {/* Left: Watchlist back, Symbol, Name, Live Price & Circuits */}
        <div className="row" style={{ gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <a
            href="/"
            className="nav-link"
            style={{
              padding: "4px 8px",
              fontSize: 12.5,
              borderRadius: 6,
              border: "1px solid var(--line)",
              background: "var(--bg-elevated)",
              fontWeight: 600,
            }}
          >
            ← Watchlist
          </a>
          <div className="row" style={{ gap: 8, alignItems: "baseline" }}>
            <h2 className="stock-title" style={{ margin: 0, fontSize: 18, fontWeight: 800 }}>
              {d?.symbol ?? k}
            </h2>
            {d && (
              <span className="muted" style={{ fontSize: 13 }}>
                {d.name}{d.sector ? ` · ${d.sector}` : ""}
              </span>
            )}
          </div>

          {d && (d.quote || d.last?.close) && (
            <div className="row" style={{ gap: 8, alignItems: "center" }}>
              <span style={{ fontSize: 20, fontWeight: 800, fontVariantNumeric: "tabular-nums" }}>
                ₹{fmt(d.quote?.ltp ?? d.last?.close)}
              </span>
              {d.quote && (
                <span
                  className={`pill ${d.quote.net_change >= 0 ? "on" : "off"}`}
                  style={{
                    fontWeight: 700,
                    fontSize: 12,
                    padding: "2px 8px",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {d.quote.net_change >= 0 ? "+" : ""}{fmt(d.quote.net_change)} ({d.quote.net_change >= 0 ? "+" : ""}{fmt(d.quote.pct_change)}%)
                </span>
              )}
              {d.quote?.is_upper_circuit && (
                <span className="pill on" style={{ fontSize: 11, padding: "2px 7px", fontWeight: 700 }}>
                  ⚡ Upper Circuit (20%)
                </span>
              )}
              {d.quote?.is_lower_circuit && (
                <span className="pill off" style={{ fontSize: 11, padding: "2px 7px", fontWeight: 700 }}>
                  ⚠️ Lower Circuit
                </span>
              )}
            </div>
          )}
        </div>

        {/* Right: Trend Radar Switcher & Side Panel Toggle */}
        <div className="row" style={{ gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          <span className="muted small" style={{ fontWeight: 600, fontSize: 11.5, marginRight: 2 }}>
            Trend:
          </span>
          {TFS.map((t) => {
            const v = trends?.[t];
            const active = t === tf;
            return (
              <button
                key={t}
                onClick={() => setTf(t)}
                className={`pill ${v ? TREND_CLASS[v] : ""}`}
                style={{
                  cursor: "pointer",
                  fontWeight: active ? 700 : 500,
                  fontSize: 11.5,
                  padding: "3px 8px",
                  borderColor: active ? "var(--accent)" : undefined,
                  background: active ? "var(--card-hover)" : undefined,
                  boxShadow: active ? "0 0 0 1px var(--accent)" : undefined,
                }}
                title={`Switch to ${t}${isLive(t) ? " (" + LIVE_LABEL + ")" : ""}`}
              >
                {t} {v ?? "–"}{isLive(t) ? " · live" : ""}
              </button>
            );
          })}
          <button
            className="btn small"
            onClick={() => setShowSide(!showSide)}
            style={{
              cursor: "pointer",
              marginLeft: 4,
              fontSize: 11.5,
              padding: "4px 10px",
              background: showSide ? "var(--card-hover)" : "var(--bg-elevated)",
              border: "1px solid var(--line)",
              borderRadius: 6,
            }}
            title={showSide ? "Hide side panel for full-width chart" : "Show side panel"}
          >
            {showSide ? "◫ Hide Panel" : "◫ Show Panel"}
          </button>
        </div>
      </div>

      {/* TOP POSITION: The Chart Grid */}
      {tf && (
        <div
          className="chartgrid"
          style={{
            gridTemplateColumns: showSide ? undefined : "1fr",
            marginBottom: 12,
            alignItems: "start",
          }}
        >
          {/* Main Candlestick Chart */}
          <div style={{ minWidth: 0 }}>
            <KChart instrumentKey={k} tf={tf} setTf={setTf} onData={onData} />
            {d && (
              <p className="muted small" style={{ marginTop: 6, lineHeight: 1.4, fontSize: 11.5 }}>
                {d.sources.filter((s) => s.bars > 0).map((s) => `${s.source}: last bar ${s.last_time}`).join(" · ")}. Newest bars may lag.
                {d.adjusted
                  ? " Daily bars split/bonus adjusted."
                  : " Intraday bars are as the vendor served them (not split/bonus adjusted); the recorder starts 09:21 before the switch, so today's first bar can be partial."}
                {" "}Zones are computed on this timeframe's bars. Drawings are saved in this browser only.
              </p>
            )}
          </div>

          {/* Right Companion Panel: Rule Verdict & Timeframe Bias */}
          {showSide && (
            <aside className="alertside" style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 10 }}>
              <VerdictBar instrumentKey={k} tf={tf} />
              {d && d.tf === tf && (
                <div className="card bias" style={{ padding: "10px 12px" }}>
                  <div className="pill warn" style={{ marginBottom: 6, fontSize: 11, fontWeight: 700 }}>
                    {d.bias.label}
                  </div>
                  {isLive(d.tf) && (
                    <div className="pill warn" style={{ marginBottom: 6, fontSize: 11 }}>
                      {LIVE_LABEL}
                    </div>
                  )}
                  <b className="small">Rule summary · {d.tf}</b>
                  <ul style={{ margin: "4px 0 0 16px", padding: 0, fontSize: 12.5 }}>
                    {d.bias.rules.map((r) => (
                      <li key={r.rule} style={{ marginBottom: 3 }}>{r.text}</li>
                    ))}
                  </ul>
                </div>
              )}
            </aside>
          )}
        </div>
      )}

      {/* Below the Chart: AI Trading Intelligence Breakdown & Deep Analysis */}
      <AiAnalysisCard instrumentKey={k} />
      <AnalysisPanel instrumentKey={k} />
    </div>
  );
}
