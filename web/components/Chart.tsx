"use client";
import { useEffect, useRef, useState } from "react";
import {
  CandlestickSeries, ColorType, createChart, createSeriesMarkers, HistogramSeries, IChartApi,
  ISeriesApi, LineSeries, LineStyle, SeriesMarker, Time,
} from "lightweight-charts";

export type ChartData = {
  key: string; symbol: string | null; name: string | null; sector: string | null; last_bar_source: string;
  bars: { time: string; open: number; high: number; low: number; close: number; volume: number }[];
  lines: Record<"ema20" | "sma50" | "sma200", { time: string; value: number }[]>;
  zones: { price: number; bottom: number; top: number; touches: number; first: string; last: string }[];
  candles: { time: string; flags: string[] }[];
  bullish_flags: string[];
  last: { atr: number | null; rsi: number | null; atr_pct: number | null };
  watchlist_dates: string[];
  signals: unknown[];
};

const LINE_COLORS = { ema20: "#e0a100", sma50: "#3a86ff", sma200: "#9d4edd" } as const;
const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

export default function Chart({ data }: { data: ChartData }) {
  const box = useRef<HTMLDivElement>(null);
  const [show, setShow] = useState({ ema20: true, sma50: true, sma200: true, zones: true, flags: "bullish" as "off" | "bullish" | "all", watch: true });

  useEffect(() => {
    if (!box.current) return;
    const up = css("--up"), down = css("--down"), fg = css("--fg"), line = css("--line"), muted = css("--muted");
    const chart: IChartApi = createChart(box.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: css("--bg") }, textColor: fg },
      grid: { vertLines: { color: line }, horzLines: { color: line } },
      rightPriceScale: { borderColor: line }, timeScale: { borderColor: line },
    });
    const c: ISeriesApi<"Candlestick"> = chart.addSeries(CandlestickSeries, {
      upColor: up, downColor: down, borderUpColor: up, borderDownColor: down, wickUpColor: up, wickDownColor: down,
    });
    c.setData(data.bars.map((b) => ({ ...b, time: b.time as Time })));
    const v = chart.addSeries(HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    v.setData(data.bars.map((b) => ({ time: b.time as Time, value: b.volume, color: b.close >= b.open ? up + "66" : down + "66" })));
    (["ema20", "sma50", "sma200"] as const).forEach((n) => {
      if (!show[n]) return;
      const s = chart.addSeries(LineSeries, { color: LINE_COLORS[n], lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: n.toUpperCase() });
      s.setData(data.lines[n].map((p) => ({ time: p.time as Time, value: p.value })));
    });
    if (show.zones) {
      data.zones.forEach((z) => {
        c.createPriceLine({ price: z.top, color: muted, lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: "" });
        c.createPriceLine({ price: z.bottom, color: muted, lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: "" });
        c.createPriceLine({ price: z.price, color: muted, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: `zone ×${z.touches}` });
      });
    }
    const marks: SeriesMarker<Time>[] = [];
    if (show.flags !== "off") {
      const bull = new Set(data.bullish_flags);
      data.candles.forEach((m) => {
        const fl = show.flags === "all" ? m.flags : m.flags.filter((f) => bull.has(f));
        if (fl.length) marks.push({ time: m.time as Time, position: "belowBar", color: bull.has(fl[0]) ? up : muted, shape: "arrowUp", text: fl.join(", ") });
      });
    }
    if (show.watch) {
      data.watchlist_dates.forEach((d) => marks.push({ time: d as Time, position: "aboveBar", color: css("--accent"), shape: "circle", text: "WL" }));
    }
    marks.sort((a, b) => String(a.time).localeCompare(String(b.time)));
    createSeriesMarkers(c, marks);
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, data.bars.length - 160), to: data.bars.length + 3 });
    return () => chart.remove();
  }, [data, show]);

  const tog = (k: "ema20" | "sma50" | "sma200" | "zones" | "watch") => (
    <label className="tog"><input type="checkbox" checked={show[k]} onChange={(e) => setShow({ ...show, [k]: e.target.checked })} />{k.toUpperCase()}</label>
  );
  return (
    <>
      <div className="row" style={{ marginBottom: 6 }}>
        {tog("ema20")}{tog("sma50")}{tog("sma200")}{tog("zones")}{tog("watch")}
        <label className="tog">candle flags
          <select value={show.flags} onChange={(e) => setShow({ ...show, flags: e.target.value as "off" | "bullish" | "all" })}>
            <option value="bullish">trigger flags (hammer, engulfing, morning star)</option>
            <option value="all">all 26</option>
            <option value="off">off</option>
          </select>
        </label>
      </div>
      <div ref={box} style={{ width: "100%", height: "min(70vh, 620px)" }} />
    </>
  );
}
