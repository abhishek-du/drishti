"use client";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import type { BarsData, Tf } from "@/components/kchart/KChart";
import { get } from "@/components/api";
import { LIVE_LABEL } from "@/components/live";
// klinecharts touches `window` when imported, so the chart loads in the browser only (no SSR)
const KChart = dynamic(() => import("@/components/kchart/KChart"), { ssr: false, loading: () => <p className="muted">Loading chart…</p> });
const TFS: Tf[] = ["1m", "5m", "15m", "1h", "1d"];
const isLive = (tf: Tf) => tf === "1m" || tf === "5m";

type Trends = { trends: Record<Tf, "UP" | "DOWN" | "SIDE" | null> };
const TREND_CLASS = { UP: "on", DOWN: "off", SIDE: "" } as const;

function tfFromUrl(): Tf {
  const q = new URLSearchParams(location.search);
  const tf = q.get("tf") as Tf | null;
  if (tf && TFS.includes(tf)) return tf;
  return q.get("tab") === "live" ? "1m" : "1d";   // old ?tab=live links
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
    u.searchParams.set("tf", t); u.searchParams.delete("tab");
    history.replaceState(null, "", u);
  };
  const onData = useCallback((x: BarsData | null) => setD(x), []);
  useEffect(() => {
    get<Trends>(`/api/chart2/trends?key=${encodeURIComponent(k)}`).then((x) => setTrends(x.trends)).catch(() => setTrends(null));
  }, [k]);

  return (
    <div className="stockpage">
      <div className="row" style={{ marginBottom: 6 }}>
        <h2 style={{ margin: 0 }}>{d?.symbol ?? k}</h2>
        {d && <span className="muted">{d.name}{d.sector ? ` · ${d.sector}` : ""}</span>}
      </div>
      <div className="row trends" aria-label="Trend per timeframe (spec section 3 rule)">
        <span className="muted">trend (spec §3 rule):</span>
        {TFS.map((t) => {
          const v = trends?.[t];
          return (
            <span key={t} className={`pill ${v ? TREND_CLASS[v] : ""}`} style={{ fontWeight: t === tf ? 700 : 400 }} title={isLive(t) ? LIVE_LABEL : undefined}>
              {t} {v ?? "–"}{isLive(t) ? " · live view" : ""}
            </span>
          );
        })}
      </div>
      {tf && (
        <div className="chartgrid">
          <div style={{ minWidth: 0 }}>
            <KChart instrumentKey={k} tf={tf} setTf={setTf} onData={onData} />
            {d && (
              <p className="muted small">
                {d.sources.filter((s) => s.bars > 0).map((s) => `${s.source}: last bar ${s.last_time}`).join(" · ")}. Newest bars may lag.
                {d.adjusted ? " Daily bars split/bonus adjusted." : " Intraday bars are as the vendor served them (not split/bonus adjusted); the recorder starts 09:21 before the switch, so today's first bar can be partial."}
                {" "}Zones are computed on this timeframe's bars. Drawings are saved in this browser only.
              </p>
            )}
          </div>
          {d && d.tf === tf && (
            <aside className="card bias">
              <div className="pill warn" style={{ marginBottom: 6 }}>{d.bias.label}</div>
              {isLive(d.tf) && <div className="pill warn" style={{ marginBottom: 6 }}>{LIVE_LABEL}</div>}
              <b>Rule summary · {d.tf}</b>
              <ul>{d.bias.rules.map((r) => <li key={r.rule}>{r.text}</li>)}</ul>
            </aside>
          )}
        </div>
      )}
    </div>
  );
}
