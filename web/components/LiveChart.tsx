"use client";
import { useEffect, useRef, useState } from "react";
import { CandlestickSeries, ColorType, createChart, HistogramSeries, IChartApi, ISeriesApi, LineSeries, Time, UTCTimestamp } from "lightweight-charts";
import { LIVE_LABEL, LiveCandle, LiveMsg, openLive, stale } from "./live";
import { fmt } from "./api";

const IST = 19800; // lightweight-charts draws UTC; shift so the axis reads IST
const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

export default function LiveChart({ instrumentKey }: { instrumentKey: string }) {
  const box = useRef<HTMLDivElement>(null);
  const series = useRef<{ c: ISeriesApi<"Candlestick">; v: ISeriesApi<"Histogram">; w: ISeriesApi<"Line"> } | null>(null);
  const [tf, setTf] = useState(1);
  const [msg, setMsg] = useState<LiveMsg | null>(null);

  useEffect(() => {
    if (!box.current) return;
    const up = css("--up"), down = css("--down"), line = css("--line");
    const chart: IChartApi = createChart(box.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: css("--bg") }, textColor: css("--fg") },
      grid: { vertLines: { color: line }, horzLines: { color: line } },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: line }, rightPriceScale: { borderColor: line },
    });
    const c = chart.addSeries(CandlestickSeries, { upColor: up, downColor: down, borderUpColor: up, borderDownColor: down, wickUpColor: up, wickDownColor: down });
    const v = chart.addSeries(HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    const w = chart.addSeries(LineSeries, { color: "#e0a100", lineWidth: 2, title: "VWAP", priceLineVisible: false });
    series.current = { c, v, w };
    const close = openLive((m) => {
      setMsg(m);
      const cs: LiveCandle[] = m.candles ?? [];
      const t = (x: LiveCandle) => (x.time + IST) as UTCTimestamp as Time;
      c.setData(cs.map((x) => ({ time: t(x), open: x.open, high: x.high, low: x.low, close: x.close })));
      v.setData(cs.map((x) => ({ time: t(x), value: x.volume, color: x.close >= x.open ? up + "66" : down + "66" })));
      w.setData(cs.filter((x) => x.vwap).map((x) => ({ time: t(x), value: x.vwap as number })));
    }, instrumentKey, tf);
    return () => { close(); chart.remove(); series.current = null; };
  }, [instrumentKey, tf]);

  const q = msg?.summary.keys[instrumentKey];
  const upd = msg?.summary.updated_at;
  return (
    <>
      <div className="row" style={{ marginBottom: 6 }}>
        <span className="pill warn">{LIVE_LABEL}</span>
        <label className="tog">interval
          <select value={tf} onChange={(e) => setTf(Number(e.target.value))}><option value={1}>1m</option><option value={5}>5m</option></select>
        </label>
        <span className={`pill ${stale(upd) ? "off" : "on"}`}>{stale(upd) ? "no live data" : "live"}</span>
        {q && <span className="card">LTP {fmt(q.ltp)} · {fmt(q.chg_pct)}% · VWAP {fmt(q.vwap)} · vol {q.vtt?.toLocaleString("en-IN") ?? "–"}</span>}
      </div>
      <div ref={box} style={{ width: "100%", height: "min(60vh, 520px)" }} />
      <p className="muted">Built live from the Upstox full feed (09:21–15:35 before the switch, from 09:15 after it). VWAP = the exchange's average traded price. Volume of the first minute after connecting is not shown (baseline).</p>
    </>
  );
}
