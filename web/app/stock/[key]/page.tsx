"use client";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import type { BarsData, Tf } from "@/components/kchart/KChart";
import { fmt, get } from "@/components/api";
import { LIVE_LABEL } from "@/components/live";
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
      {/* Stock Header & Breadcrumb */}
      <div className="row" style={{ marginBottom: 8, justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
        <div className="row" style={{ gap: 12, alignItems: "center" }}>
          <a href="/" className="nav-link" style={{ padding: "4px 8px", fontSize: 13 }}>
            ← Watchlist
          </a>
          <h2 className="stock-title" style={{ margin: 0 }}>{d?.symbol ?? k}</h2>
          {d && (
            <span className="muted" style={{ fontSize: 14 }}>
              {d.name}{d.sector ? ` · ${d.sector}` : ""}
            </span>
          )}
        </div>

        {d && (d.quote || d.last?.close) && (
          <div className="row" style={{ gap: 10, alignItems: "center" }}>
            <span style={{ fontSize: 22, fontWeight: 800, fontVariantNumeric: "tabular-nums" }}>
              ₹{fmt(d.quote?.ltp ?? d.last?.close)}
            </span>
            {d.quote && (
              <span style={{
                color: d.quote.net_change >= 0 ? "var(--up)" : "var(--down)",
                fontWeight: 700,
                fontSize: 14,
                fontVariantNumeric: "tabular-nums"
              }}>
                {d.quote.net_change >= 0 ? "+" : ""}{fmt(d.quote.net_change)} ({d.quote.net_change >= 0 ? "+" : ""}{fmt(d.quote.pct_change)}%)
              </span>
            )}
            {d.quote?.is_upper_circuit && (
              <span className="pill on" style={{ fontSize: 11, padding: "2px 8px", fontWeight: 700 }}>
                ⚡ Upper Circuit Limit (20%)
              </span>
            )}
            {d.quote?.is_lower_circuit && (
              <span className="pill off" style={{ fontSize: 11, padding: "2px 8px", fontWeight: 700 }}>
                ⚠️ Lower Circuit Limit
              </span>
            )}
          </div>
        )}
      </div>

      {/* Multi-timeframe trend radar badges */}
      <div className="row trends" aria-label="Trend per timeframe (spec section 3 rule)">
        <span className="muted small" style={{ fontWeight: 600 }}>Trend radar (spec §3):</span>
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
      </div>

      {tf && (
        <div className="chartgrid">
          <div style={{ minWidth: 0 }}>
            <VerdictBar instrumentKey={k} tf={tf} />
            <KChart instrumentKey={k} tf={tf} setTf={setTf} onData={onData} />
            {d && (
              <p className="muted small" style={{ marginTop: 8, lineHeight: 1.4 }}>
                {d.sources.filter((s) => s.bars > 0).map((s) => `${s.source}: last bar ${s.last_time}`).join(" · ")}. Newest bars may lag.
                {d.adjusted
                  ? " Daily bars split/bonus adjusted."
                  : " Intraday bars are as the vendor served them (not split/bonus adjusted); the recorder starts 09:21 before the switch, so today's first bar can be partial."}
                {" "}Zones are computed on this timeframe's bars. Drawings are saved in this browser only.
              </p>
            )}
          </div>

          {d && d.tf === tf && (
            <aside className="card bias">
              <div className="pill warn" style={{ marginBottom: 8, fontSize: 11, fontWeight: 700 }}>
                {d.bias.label}
              </div>
              {isLive(d.tf) && (
                <div className="pill warn" style={{ marginBottom: 8, fontSize: 11 }}>
                  {LIVE_LABEL}
                </div>
              )}
              <b>Rule summary · {d.tf}</b>
              <ul>
                {d.bias.rules.map((r) => (
                  <li key={r.rule}>{r.text}</li>
                ))}
              </ul>
            </aside>
          )}
        </div>
      )}
      <AnalysisPanel instrumentKey={k} />
    </div>
  );
}
