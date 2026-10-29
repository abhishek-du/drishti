"use client";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import TfChart, { BarsData, isLive, Tf, TFS } from "@/components/TfChart";
import { fmt, get } from "@/components/api";
import { LIVE_LABEL } from "@/components/live";

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
  const [err, setErr] = useState("");
  const [trends, setTrends] = useState<Trends["trends"] | null>(null);

  useEffect(() => setTfRaw(tfFromUrl()), []);
  const setTf = (t: Tf) => {
    setTfRaw(t);
    const u = new URL(location.href);
    u.searchParams.set("tf", t); u.searchParams.delete("tab");
    history.replaceState(null, "", u);
  };
  useEffect(() => {
    if (!tf) return;
    let dead = false;
    const load = () => get<BarsData>(`/api/chart2/bars?key=${encodeURIComponent(k)}&tf=${tf}`)
      .then((x) => { if (!dead) { setD(x); setErr(""); } })
      .catch((e) => { if (!dead) { setErr(String(e)); setD(null); } });
    setD(null); setErr("");
    load();
    const iv = isLive(tf) ? setInterval(load, 15000) : undefined;   // history + indicators refresh
    return () => { dead = true; clearInterval(iv); };
  }, [k, tf]);
  useEffect(() => {
    get<Trends>(`/api/chart2/trends?key=${encodeURIComponent(k)}`).then((x) => setTrends(x.trends)).catch(() => setTrends(null));
  }, [k]);

  const last = d?.bars[d.bars.length - 1];
  return (
    <>
      <div className="row" style={{ marginBottom: 8 }}>
        <h2 style={{ margin: 0 }}>{d?.symbol ?? k}</h2>
        {d && <span className="muted">{d.name}{d.sector ? ` · ${d.sector}` : ""}</span>}
        {last && <span className="card">close {fmt(last.close)} · ATR {fmt(d?.last.atr)} · RSI {fmt(d?.last.rsi, 0)}</span>}
      </div>

      <div className="row trends" aria-label="Trend per timeframe (spec section 3 rule)">
        <span className="muted">trend (spec §3 rule):</span>
        {TFS.map((t) => {
          const v = trends?.[t];
          return (
            <span key={t} className={`pill ${v ? TREND_CLASS[v] : ""}`} style={{ fontWeight: t === tf ? 700 : 400 }}
              title={isLive(t) ? LIVE_LABEL : undefined}>
              {t} {v ?? "–"}{isLive(t) ? " · live view" : ""}
            </span>
          );
        })}
      </div>

      <div className="row" style={{ margin: "8px 0" }}>
        {TFS.map((t) => (
          <button key={t} onClick={() => setTf(t)} aria-pressed={t === tf} className={t === tf ? "sel" : ""}>{t}</button>
        ))}
        {tf && isLive(tf) && <span className="pill warn">{LIVE_LABEL}</span>}
      </div>

      {err && <p className="muted">{err}</p>}
      {!d && !err && <p className="muted">Loading…</p>}
      {d && tf && d.tf === tf && (
        <div className="chartgrid">
          <div style={{ minWidth: 0 }}>
            <TfChart data={d} instrumentKey={k} />
            <p className="muted small">
              {d.sources.filter((s) => s.bars > 0).map((s) => `${s.source}: last bar ${s.last_time}`).join(" · ")}.
              {" "}Newest bars may lag.
              {d.adjusted ? " Daily bars split/bonus adjusted." : " Intraday bars are as the vendor served them (not split/bonus adjusted); the recorder starts 09:21 before the switch, so today's first bar can be partial."}
              {" "}Zones are computed on this timeframe's bars. Indicators and patterns: drishti.features (Bollinger, MACD, VWAP, trend: interim chart stubs).
            </p>
          </div>
          <aside className="card bias">
            <div className="pill warn" style={{ marginBottom: 6 }}>{d.bias.label}</div>
            {isLive(d.tf) && <div className="pill warn" style={{ marginBottom: 6 }}>{LIVE_LABEL}</div>}
            <b>Rule summary · {d.tf}</b>
            <ul>{d.bias.rules.map((r) => <li key={r.rule}>{r.text}</li>)}</ul>
          </aside>
        </div>
      )}
    </>
  );
}
