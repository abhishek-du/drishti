"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries, ColorType, createChart, createSeriesMarkers, HistogramSeries, IChartApi,
  ISeriesApi, LineSeries, LineStyle, LogicalRange, SeriesMarker, Time, UTCTimestamp,
} from "lightweight-charts";
import { fmt } from "./api";
import { LIVE_LABEL, LiveCandle, openLive } from "./live";

export type Tf = "1m" | "5m" | "15m" | "1h" | "1d";
export const TFS: Tf[] = ["1m", "5m", "15m", "1h", "1d"];
export const isLive = (tf: Tf) => tf === "1m" || tf === "5m";
type Pt = { time: string | number; value: number };
type Line = "ema20" | "sma50" | "sma200" | "vwap" | "bb_mid" | "bb_up" | "bb_lo";
type Pane = "rsi" | "macd" | "macd_signal" | "macd_hist";
export type Zone = { price: number; bottom: number; top: number; touches: number; first: string | number; last: string | number };
export type BarsData = {
  key: string; tf: Tf; label: string | null; adjusted: boolean;
  sources: { source: string; bars: number; last_time: string | null }[];
  symbol: string | null; name: string | null; sector: string | null; watchlist_dates: string[];
  bars: { time: string | number; open: number; high: number; low: number; close: number; volume: number; chg_pct: number | null }[];
  lines: Record<Line, Pt[]>; panes: Record<Pane, Pt[]>;
  candles: { time: string | number; flags: string[] }[]; bullish_flags: string[]; zones: Zone[];
  trend: "UP" | "DOWN" | "SIDE";
  last: { ema20: number | null; sma50: number | null; vwap: number | null; rsi: number | null; atr: number | null; close: number | null };
  bias: { label: string; text: string; rules: { rule: string; state: unknown; text: string }[] };
};

const IST = 19800; // lightweight-charts draws UTC; shift intraday epochs so the axis reads IST
const COLORS = { ema20: "#e0a100", sma50: "#3a86ff", sma200: "#9d4edd", vwap: "#e76f51", bb: "#2a9d8f", macd: "#3a86ff", signal: "#e0a100" };
const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const tt = (t: string | number) => (typeof t === "number" ? ((t + IST) as UTCTimestamp) : t) as Time;
const tkey = (t: Time) => String(t);

type Show = { ema20: boolean; sma50: boolean; sma200: boolean; vwap: boolean; bb: boolean; volume: boolean; zones: boolean; rsi: boolean; macd: boolean; watch: boolean; flags: "bullish" | "all" | "off" };
const DEFAULT: Show = { ema20: true, sma50: true, sma200: true, vwap: true, bb: false, volume: true, zones: true, rsi: true, macd: false, watch: true, flags: "bullish" };
const STORE = "drishti.chart.show";

function loadShow(): Show {
  try { return { ...DEFAULT, ...JSON.parse(localStorage.getItem(STORE) || "{}") }; } catch { return DEFAULT; }
}

type Tip = { x: number; y: number; i: number } | null;

export default function TfChart({ data, instrumentKey }: { data: BarsData; instrumentKey: string }) {
  const box = useRef<HTMLDivElement>(null);
  const range = useRef<{ tf: Tf; r: LogicalRange | null } | null>(null);
  const [show, setShowRaw] = useState<Show>(DEFAULT);
  const [tip, setTip] = useState<Tip>(null);
  const tf = data.tf;
  useEffect(() => setShowRaw(loadShow()), []);
  const setShow = (s: Show) => { setShowRaw(s); try { localStorage.setItem(STORE, JSON.stringify(s)); } catch { /* storage blocked */ } };

  // time -> value lookups for the tooltip (server values only; nothing is computed here)
  const look = useMemo(() => {
    const idx = new Map<string, number>();
    data.bars.forEach((b, i) => idx.set(tkey(tt(b.time)), i));
    const m = (pts: Pt[]) => new Map(pts.map((p) => [tkey(tt(p.time)), p.value]));
    const flags = new Map(data.candles.map((c) => [tkey(tt(c.time)), c.flags]));
    return {
      idx, flags,
      lines: Object.fromEntries(Object.entries(data.lines).map(([k, v]) => [k, m(v)])) as Record<Line, Map<string, number>>,
      panes: Object.fromEntries(Object.entries(data.panes).map(([k, v]) => [k, m(v)])) as Record<Pane, Map<string, number>>,
    };
  }, [data]);

  useEffect(() => {
    if (!box.current) return;
    const up = css("--up"), down = css("--down"), line = css("--line"), muted = css("--muted");
    const chart: IChartApi = createChart(box.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: css("--bg") }, textColor: css("--fg"), panes: { separatorColor: line } },
      grid: { vertLines: { color: line }, horzLines: { color: line } },
      rightPriceScale: { borderColor: line },
      timeScale: { borderColor: line, timeVisible: tf !== "1d", secondsVisible: false },
    });
    const c: ISeriesApi<"Candlestick"> = chart.addSeries(CandlestickSeries, {
      upColor: up, downColor: down, borderUpColor: up, borderDownColor: down, wickUpColor: up, wickDownColor: down,
    });
    c.setData(data.bars.map((b) => ({ time: tt(b.time), open: b.open, high: b.high, low: b.low, close: b.close })));
    let vol: ISeriesApi<"Histogram"> | null = null;
    if (show.volume) {
      vol = chart.addSeries(HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
      chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      vol.setData(data.bars.map((b) => ({ time: tt(b.time), value: b.volume, color: b.close >= b.open ? up + "66" : down + "66" })));
    }
    const addLine = (pts: Pt[], color: string, title: string, pane = 0, width: 1 | 2 = 1, style = LineStyle.Solid) => {
      const s = chart.addSeries(LineSeries, { color, lineWidth: width, lineStyle: style, priceLineVisible: false, lastValueVisible: false, title }, pane);
      s.setData(pts.map((p) => ({ time: tt(p.time), value: p.value })));
      return s;
    };
    (["ema20", "sma50", "sma200"] as const).forEach((n) => { if (show[n]) addLine(data.lines[n], COLORS[n], n.toUpperCase()); });
    if (show.vwap && tf !== "1d") addLine(data.lines.vwap, COLORS.vwap, "VWAP", 0, 2);
    if (show.bb) {
      addLine(data.lines.bb_up, COLORS.bb, "BB up", 0, 1, LineStyle.Dashed);
      addLine(data.lines.bb_mid, COLORS.bb, "BB mid", 0, 1, LineStyle.Dotted);
      addLine(data.lines.bb_lo, COLORS.bb, "BB lo", 0, 1, LineStyle.Dashed);
    }
    let pane = 0;
    if (show.rsi) {
      pane += 1;
      const r = addLine(data.panes.rsi, COLORS.sma200, "RSI 14", pane, 1);
      r.applyOptions({ lastValueVisible: true });
      [30, 70].forEach((p) => r.createPriceLine({ price: p, color: muted, lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: "" }));
    }
    if (show.macd) {
      pane += 1;
      const h = chart.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false, title: "hist" }, pane);
      h.setData(data.panes.macd_hist.map((p) => ({ time: tt(p.time), value: p.value, color: p.value >= 0 ? up + "88" : down + "88" })));
      addLine(data.panes.macd, COLORS.macd, "MACD 12,26,9", pane);
      addLine(data.panes.macd_signal, COLORS.signal, "signal", pane);
    }
    chart.panes().forEach((p, i) => p.setStretchFactor(i === 0 ? 4 : 1));   // ~440 px price, ~110 px per pane
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
        if (fl.length) marks.push({ time: tt(m.time), position: "belowBar", color: fl.some((f) => bull.has(f)) ? up : muted, shape: "arrowUp", text: fl.join(", ") });
      });
    }
    if (show.watch && tf === "1d") data.watchlist_dates.forEach((d) => marks.push({ time: d as Time, position: "aboveBar", color: css("--accent"), shape: "circle", text: "WL" }));
    marks.sort((a, b) => (look.idx.get(tkey(a.time)) ?? 0) - (look.idx.get(tkey(b.time)) ?? 0));
    createSeriesMarkers(c, marks);

    const n = data.bars.length;
    if (range.current?.tf === tf && range.current.r) chart.timeScale().setVisibleLogicalRange(range.current.r);
    else chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - (tf === "1d" ? 160 : 150)), to: n + 3 });
    chart.timeScale().subscribeVisibleLogicalRangeChange((r) => { range.current = { tf, r }; });
    chart.subscribeCrosshairMove((p) => {
      const i = p.time === undefined ? undefined : look.idx.get(tkey(p.time));
      setTip(i === undefined || !p.point ? null : { x: p.point.x, y: p.point.y, i });
    });

    // 1m/5m: the forming candle from the live WebSocket (history and indicators from the API)
    let close = () => {};
    if (isLive(tf)) {
      const lastT = data.bars.length ? Number(data.bars[n - 1].time) : 0;
      close = openLive((m) => {
        (m.candles ?? []).filter((x: LiveCandle) => x.time >= lastT).forEach((x) => {
          c.update({ time: tt(x.time), open: x.open, high: x.high, low: x.low, close: x.close });
          vol?.update({ time: tt(x.time), value: x.volume, color: x.close >= x.open ? up + "66" : down + "66" });
        });
      }, instrumentKey, tf === "5m" ? 5 : 1);
    }
    return () => { close(); chart.remove(); };
  }, [data, show, tf, look, instrumentKey]);

  const tog = (k: Exclude<keyof Show, "flags">, label: string, hide = false) => hide ? null : (
    <label className="tog"><input type="checkbox" checked={show[k]} onChange={(e) => setShow({ ...show, [k]: e.target.checked })} />{label}</label>
  );
  const panes = (show.rsi ? 1 : 0) + (show.macd ? 1 : 0);
  return (
    <>
      <div className="row toggles">
        {tog("ema20", "EMA20")}{tog("sma50", "SMA50")}{tog("sma200", "SMA200")}{tog("vwap", "VWAP", tf === "1d")}
        {tog("bb", "Bollinger 20,2")}{tog("volume", "Volume")}{tog("zones", "S/R zones")}{tog("rsi", "RSI 14")}{tog("macd", "MACD")}
        {tog("watch", "WL marks", tf !== "1d")}
        <label className="tog">patterns
          <select value={show.flags} onChange={(e) => setShow({ ...show, flags: e.target.value as Show["flags"] })}>
            <option value="bullish">trigger flags</option>
            <option value="all">all 26</option>
            <option value="off">off</option>
          </select>
        </label>
      </div>
      <div className="chartbox" style={{ height: 440 + 110 * panes }}>
        {isLive(tf) && <span className="pill warn livetag">{LIVE_LABEL}</span>}
        <div ref={box} style={{ position: "absolute", inset: 0 }} />
        {tip && <Tooltip data={data} look={look} tip={tip} show={show} width={box.current?.clientWidth ?? 0} />}
      </div>
    </>
  );
}

function Tooltip({ data, look, tip, show, width }: {
  data: BarsData; tip: NonNullable<Tip>; show: Show; width: number;
  look: { lines: Record<Line, Map<string, number>>; panes: Record<Pane, Map<string, number>>; flags: Map<string, string[]> };
}) {
  const b = data.bars[tip.i];
  const k = tkey(tt(b.time));
  const when = typeof b.time === "number"
    ? new Date((b.time + IST) * 1000).toISOString().slice(0, 16).replace("T", " ")
    : b.time;
  const v = (src: Map<string, number>, d = 2) => fmt(src.get(k), d);
  const rows: [string, string][] = [];
  if (show.ema20) rows.push(["EMA20", v(look.lines.ema20)]);
  if (show.sma50) rows.push(["SMA50", v(look.lines.sma50)]);
  if (show.sma200) rows.push(["SMA200", v(look.lines.sma200)]);
  if (show.vwap && data.tf !== "1d") rows.push(["VWAP", v(look.lines.vwap)]);
  if (show.bb) rows.push(["BB lo / mid / up", `${v(look.lines.bb_lo)} / ${v(look.lines.bb_mid)} / ${v(look.lines.bb_up)}`]);
  if (show.rsi) rows.push(["RSI 14", v(look.panes.rsi, 1)]);
  if (show.macd) rows.push(["MACD / signal / hist", `${v(look.panes.macd)} / ${v(look.panes.macd_signal)} / ${v(look.panes.macd_hist)}`]);
  const fl = look.flags.get(k);
  const left = tip.x + 240 > width ? Math.max(4, tip.x - 236) : tip.x + 14;
  return (
    <div className="tip card" style={{ left, top: Math.max(4, Math.min(tip.y + 14, 260)) }}>
      {isLive(data.tf) && <div className="pill warn" style={{ marginBottom: 4 }}>{LIVE_LABEL}</div>}
      <b>{when}</b>
      <div>O {fmt(b.open)} · H {fmt(b.high)} · L {fmt(b.low)} · C {fmt(b.close)}</div>
      <div>vol {b.volume.toLocaleString("en-IN", { maximumFractionDigits: 0 })} · <span style={{ color: (b.chg_pct ?? 0) >= 0 ? "var(--up)" : "var(--down)" }}>{fmt(b.chg_pct)}%</span></div>
      {rows.map(([a, x]) => <div key={a}><span className="muted">{a}</span> {x}</div>)}
      {fl && <div><span className="muted">patterns</span> {fl.join(", ")}</div>}
    </div>
  );
}
