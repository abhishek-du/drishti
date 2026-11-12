"use client";
// TradingView-style chart on KLineChart v10 (Apache 2.0). Bars, indicator values, candle flags,
// zones and the trend all come from the read-only API (drishti.features); this file only draws.
// Drawings are kept per stock and timeframe in this browser's localStorage (no DB writes).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Chart, dispose, Indicator, init, KLineData, Overlay, OverlayCreate } from "klinecharts";
import { fmt, get } from "../api";
import { LIVE_LABEL, LiveCandle, openLive } from "../live";
import { CATALOG, DEFAULT_SET, Ext, Instance, Kind, PALETTE, PARAM_LABEL, Params, registerTemplates, TEMPLATE, title } from "./catalog";
import { History, loadDrawings, registerOverlays, Saved, saveDrawings, TOOLS, USER } from "./overlays";
import SymbolSearch from "./SymbolSearch";

export type Tf = "1m" | "5m" | "15m" | "1h" | "1d";
export const TFS: Tf[] = ["1m", "5m", "15m", "1h", "1d"];
export const isLive = (tf: Tf) => tf === "1m" || tf === "5m";
type Pt = { time: string | number; value: number };
export type BarsData = {
  key: string; tf: Tf; label: string | null; adjusted: boolean; heikin_ashi: boolean;
  sources: { source: string; bars: number; last_time: string | null }[];
  symbol: string | null; name: string | null; sector: string | null; watchlist_dates: string[];
  quote?: { ltp: number; net_change: number; pct_change: number; open: number; high: number; low: number; volume: number; vwap: number | null; upper_circuit: number | null; lower_circuit: number | null; is_upper_circuit: boolean; is_lower_circuit: boolean } | null;
  bars: { time: string | number; open: number; high: number; low: number; close: number; volume: number; chg_pct: number | null;
          dir: "bull" | "bear" | "flat"; confirm: string | null; follow: string | null }[];
  lines: Record<string, Pt[]>; panes: Record<string, Pt[]>;
  candles: { time: string | number; flags: string[] }[]; bullish_flags: string[]; bearish_flags?: string[];
  markers?: { label: string; note: string; items: { time: string | number; kind: "bull" | "bear" | "caution"; flags: string[]; stop: number | null }[] };
  zones: { price: number; bottom: number; top: number; touches: number }[];
  trend: "UP" | "DOWN" | "SIDE";
  trend_detail: { class: "UP" | "DOWN" | "SIDE"; ma_trend: "UP" | "DOWN" | "FLAT"; text: string; note: string;
                  conditions: { name: string; ok: boolean; text: string }[] };
  last: { ema20: number | null; sma50: number | null; vwap: number | null; rsi: number | null; atr: number | null; close: number | null };
  bias: { label: string; text: string; rules: { rule: string; state: unknown; text: string }[] };
};
type CType = "candle_solid" | "candle_stroke" | "ohlc" | "line" | "area" | "ha";
const CTYPES: [CType, string][] = [["candle_solid", "Candles"], ["candle_stroke", "Hollow candles"], ["ohlc", "Bars"], ["line", "Line"], ["area", "Area"], ["ha", "Heikin Ashi"]];
type Scale = "normal" | "logarithm" | "percentage";
type Layers = { zones: boolean; patterns: "bullish" | "all" | "off"; trend: boolean; signals: boolean };
type Magnet = "normal" | "weak_magnet" | "strong_magnet";

export const ts = (t: string | number) => (typeof t === "number" ? t * 1000 : Date.parse(`${t}T00:00:00+05:30`));
const STOP_BARS = 10;
const LEGEND = "▲ = detected candlestick pattern (context only, not a signal, no validated edge); the only actionable layer is the alert levels.";   // a pattern marker's stop line runs this many bars to the right
const LS = { ind: "drishti.chart.ind.v1", ctype: "drishti.chart.ctype", scale: "drishti.chart.scale", layers: "drishti.chart.layers.v2", theme: "drishti.theme.v1", magnet: "drishti.chart.magnet" };
function ls<T>(k: string, d: T): T { try { const v = localStorage.getItem(k); return v ? (JSON.parse(v) as T) : d; } catch { return d; } }
function lsSet(k: string, v: unknown) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage blocked */ } }
const qs = (o: Record<string, string | number | boolean>) => Object.entries(o).map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&");

/** Scanner alert levels drawn on the chart (entry blue, stop red, R1/R2 green dashed, marker on bar_time). */
/** A zone as the alert saw it (scan_alert.context.zones_used): pivot dates first / last, and the
 * date the last pivot was confirmed (5 bars later). */
export type SavedZone = { name: string; bottom: number; top: number; touches?: number; first?: string; last?: string; confirmed?: string };
export type AlertLevels = { entry: number; stop: number; r1: number | null; r2: number | null; barTime: string; tf: "1d" | "15m"; zones: SavedZone[] };

export default function KChart({ instrumentKey, tf, setTf, onData, levels }: {
  instrumentKey: string; tf: Tf; setTf: (t: Tf) => void; onData: (d: BarsData | null) => void; levels?: AlertLevels;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<Chart | null>(null);
  const bars = useRef<KLineData[]>([]);
  const liveCb = useRef<((k: KLineData) => void) | null>(null);
  const hist = useRef<History>(new History([]));
  const mute = useRef(false);
  const [d, setD] = useState<BarsData | null>(null);
  const [err, setErr] = useState("");
  const [ctype, setCtype] = useState<CType>("candle_solid");
  const [scale, setScale] = useState<Scale>("normal");
  const [layers, setLayers] = useState<Layers>({ zones: true, patterns: "off", trend: true, signals: false });
  // alert panel (point in time): today's zones are off by default and never share the stock page's setting
  const [todayZ, setTodayZ] = useState(false);
  const showZones = levels ? todayZ : layers.zones;
  const levelsKey = JSON.stringify(levels ?? null);   // pages build `levels` inline: redraw only on a real change
  const [inds, setInds] = useState<Instance[]>(DEFAULT_SET);
  const [theme, setTheme] = useState<"light" | "dark">("dark");
  const [magnet, setMagnet] = useState<Magnet>("weak_magnet");
  const [locked, setLocked] = useState(false);
  const [sel, setSel] = useState<string | null>(null);
  const [pending, setPending] = useState<{ id: string; name: string } | null>(null);   // a tool being drawn
  const pendingRef = useRef(pending);
  pendingRef.current = pending;
  const [hover, setHover] = useState<{ i: number; x: number } | null>(null);   // the bar under the mouse
  const [pinned, setPinned] = useState<number | null>(null);   // the bar whose details card is open (opened by a click)
  const hoverRef = useRef(hover);
  hoverRef.current = hover;
  const [, setTick] = useState(0);              // re-render the legend when a live candle arrives
  const [indTick, setIndTick] = useState(0);     // re-fetch indicator values from API on 10s interval
  const [menu, setMenu] = useState<"" | "ind" | "settings" | "type">("");
  const [hstate, setHstate] = useState({ u: false, r: false });
  const [ready, setReady] = useState(false);

  // preferences (per browser)
  useEffect(() => {
    setCtype(ls(LS.ctype, "candle_solid")); setScale(ls(LS.scale, "normal")); setInds(ls(LS.ind, DEFAULT_SET));
    setLayers({ zones: true, patterns: "off", trend: true, signals: false, ...ls(LS.layers, {}) }); setMagnet(ls(LS.magnet, "weak_magnet"));
    // the theme key is shared with ThemeToggle, which stores the plain string (not JSON)
    let savedTheme: string | null = null;
    try { savedTheme = localStorage.getItem(LS.theme)?.replace(/"/g, "") ?? null; } catch { /* storage blocked */ }
    setTheme(savedTheme === "light" ? "light" : "dark");
    setReady(true);
  }, []);
  useEffect(() => { if (ready) document.documentElement.setAttribute("data-theme", theme); }, [theme, ready]);
  useEffect(() => {
    const onTheme = (e: Event) => {
      const t = (e as CustomEvent<"light" | "dark">).detail;
      if (t === "light" || t === "dark") setTheme(t);
    };
    window.addEventListener("drishti:theme", onTheme);
    return () => window.removeEventListener("drishti:theme", onTheme);
  }, []);

  // the chart itself
  useEffect(() => {
    if (!box.current) return;
    registerTemplates(); registerOverlays();
    const c = init(box.current, { timezone: "Asia/Kolkata", locale: "en-US" });
    if (!c) return;
    chart.current = c;
    c.setDataLoader({
      getBars: ({ type, callback }) => callback(type === "init" ? bars.current : [], false),
      subscribeBar: ({ callback }) => { liveCb.current = callback; },
      unsubscribeBar: () => { liveCb.current = null; },
    });
    // v10 passes only the crosshair coordinates (x, y, paneId); the bar comes from x
    c.subscribeAction("onCrosshairChange", (x) => {
      const cr = (x ?? {}) as { x?: number; y?: number; paneId?: string };
      if (typeof cr.x !== "number") { setHover(null); return; }
      const pt = c.convertFromPixel([{ x: cr.x, y: cr.y ?? 0 }], { paneId: cr.paneId ?? "candle_pane" }) as { dataIndex?: number }[];
      const n = c.getDataList().length;
      const i = pt[0]?.dataIndex;
      setHover(typeof i === "number" && n ? { i: Math.max(0, Math.min(n - 1, i)), x: cr.x } : null);
    });
    const leave = () => setHover(null);
    box.current.addEventListener("mouseleave", leave);
    // a click on a bar opens its details card (not while a drawing tool is active)
    // decided at mousedown: the click that completes a drawing (the tool is cleared on that click)
    // must not open the card
    let drawingAtDown = false;
    const down = () => { drawingAtDown = !!pendingRef.current; };
    const click = () => { if (!drawingAtDown && !pendingRef.current) setPinned(hoverRef.current ? hoverRef.current.i : null); };
    box.current.addEventListener("mousedown", down, true);
    box.current.addEventListener("click", click);
    const ro = new ResizeObserver(() => c.resize());
    ro.observe(box.current);
    const fs = () => setTimeout(() => c.resize(), 50);
    document.addEventListener("fullscreenchange", fs);
    const el = box.current;
    return () => { ro.disconnect(); el.removeEventListener("mouseleave", leave); el.removeEventListener("click", click); el.removeEventListener("mousedown", down, true); document.removeEventListener("fullscreenchange", fs); dispose(c); chart.current = null; };
  }, []);

  useEffect(() => {
    const c = chart.current;
    if (!c) return;
    const dark = theme === "dark";
    c.setStyles(theme);
    c.setStyles({
      grid: { horizontal: { color: dark ? "#262a31" : "#eef0f3" }, vertical: { color: dark ? "#262a31" : "#eef0f3" } },
      candle: {
        type: ctype === "ha" ? "candle_solid" : ctype === "line" ? "area" : ctype,
        tooltip: { showRule: "none" },
        area: ctype === "line" ? { lineSize: 2, backgroundColor: "rgba(0,0,0,0)" } : { lineSize: 2, backgroundColor: [{ offset: 0, color: "rgba(58,134,255,0.02)" }, { offset: 1, color: "rgba(58,134,255,0.25)" }] },
      },
      indicator: { tooltip: { showRule: "always" } },
    });
  }, [theme, ctype, ready]);
  useEffect(() => { chart.current?.overrideYAxis({ paneId: "candle_pane", name: scale }); }, [scale, d]);

  // bars for (key, tf, Heikin Ashi)
  const ha = ctype === "ha";
  const load = useCallback(async () => {
    try {
      const x = await get<BarsData>(`/api/chart2/bars?${qs({ key: instrumentKey, tf, ha })}`);
      setD(x); setErr(""); onData(x);
      return x;
    } catch (e) { setErr(String(e)); setD(null); onData(null); return null; }
  }, [instrumentKey, tf, ha, onData]);
  useEffect(() => { if (ready) load(); }, [load, ready]);

  // a new data set: hand it to the chart, then Drishti layers, indicators and saved drawings
  useEffect(() => {
    const c = chart.current;
    if (!c || !d) return;
    bars.current = d.bars.map((b) => ({ timestamp: ts(b.time), open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume }));
    c.setSymbol({ ticker: `${d.key}|${d.tf}|${d.heikin_ashi ? "ha" : ""}`, pricePrecision: 2, volumePrecision: 0 });
    c.setPeriod(d.tf === "1d" ? { type: "day", span: 1 } : d.tf === "1h" ? { type: "hour", span: 1 } : { type: "minute", span: parseInt(d.tf) });
    c.resetData();
  }, [d]);

  // scanner alert levels (only when a page passes them)
  useEffect(() => {
    const c = chart.current;
    if (!c || !d || !bars.current.length) return;
    c.removeOverlay({ groupId: "alert" });
    if (!levels) return;
    const t1 = bars.current[bars.current.length - 1].timestamp;
    const line = (value: number, label: string, color: string, dashed: boolean) => ({ name: "d_level", groupId: "alert", lock: true,
      extendData: { label: `${label} ${value.toFixed(2)}`, color, dashed }, points: [{ timestamp: t1, value }] }) as OverlayCreate;
    const ov: OverlayCreate[] = [line(levels.entry, "Entry", "#3a86ff", false), line(levels.stop, "Stop", "#e03131", false)];
    if (levels.r1 !== null) ov.push(line(levels.r1, "R1", "#2f9e44", true));
    if (levels.r2 !== null) ov.push(line(levels.r2, "R2", "#2f9e44", true));
    // the alert's own R1 / R2 bands, as saved with the alert (not today's zones)
    levels.zones.forEach((z) => ov.push({ name: "d_band", groupId: "alert", lock: true,
      extendData: { top: z.top, bottom: z.bottom, label: `${z.name.toUpperCase()} zone (alert's) ${z.bottom.toFixed(2)}–${z.top.toFixed(2)}${z.first ? ` · pivots ${z.first.slice(0, 10)} → ${(z.last ?? "").slice(0, 10)}` : ""}${z.confirmed ? ` · confirmed ${z.confirmed.slice(0, 10)}` : ""}` },
      points: [{ timestamp: t1, value: z.top }] } as OverlayCreate));
    // the alert bar: 1d bars are keyed by the IST date, intraday bars by their start time
    const at = levels.tf === "1d" ? ts(new Date(levels.barTime).toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" })) : Date.parse(levels.barTime);
    const bar = bars.current.find((k) => k.timestamp === at);
    if (bar && (levels.tf === d.tf)) ov.push({ name: "d_mark", groupId: "alert", lock: true, extendData: "ALERT", points: [{ timestamp: at, value: bar.high }] } as OverlayCreate);
    c.createOverlay(ov);
  }, [d, levelsKey]);  // eslint-disable-line react-hooks/exhaustive-deps

  // Drishti layers
  useEffect(() => {
    const c = chart.current;
    if (!c || !d || !bars.current.length) return;
    c.removeOverlay({ groupId: "drishti" });
    const t0 = bars.current[0].timestamp, t1 = bars.current[bars.current.length - 1].timestamp;
    const ov: OverlayCreate[] = [];
    if (showZones && d.zones.length) ov.push({ name: "d_zones", groupId: "drishti", lock: true,
      extendData: d.zones.map((z) => ({ top: z.top, bottom: z.bottom, label: `${levels ? "Today's zone (not the alert's) · " : ""}S/R ×${z.touches} · ${z.price.toFixed(2)}` })),
      points: [{ timestamp: t0, value: d.zones[0].price }] });
    if (layers.patterns !== "off") {
      // one rule for every bar: the same pattern always gets the same small muted glyph
      // (▲ under the low = bullish pattern, ▼ over the high = bearish, · = neutral); context only
      const bull = new Set(d.bullish_flags), bear = new Set(d.bearish_flags ?? []);
      const bar = new Map(d.bars.map((b) => [ts(b.time), b]));
      d.candles.forEach((m) => {
        const t = ts(m.time), b = bar.get(t);
        if (!b) return;
        const fl = layers.patterns === "all" ? m.flags : m.flags.filter((f) => bull.has(f));
        const kinds = new Set(fl.map((f) => (bull.has(f) ? "bull" : bear.has(f) ? "bear" : "neutral")));
        kinds.forEach((kind) => ov.push({ name: "d_pat", groupId: "drishti", lock: true, extendData: { kind },
          points: [{ timestamp: t, value: kind === "bear" ? b.high : b.low }] }));
      });
    }
    if (layers.signals && d.markers) {
      // server-side, causal tags (no repaint) on the bar that confirmed a trigger pattern, only when
      // close >= EMA20 or at a support known then (bear: mirror); bull also shows the pattern bar's
      // low; a bear tag in an uptrend is a faint 'caution'. Context only: the alerts are actionable.
      const idx = new Map(d.bars.map((b, i) => [ts(b.time), i]));
      d.markers.items.forEach((m) => {
        const t = ts(m.time), i = idx.get(t);
        if (i === undefined) return;
        const b = d.bars[i], name = m.flags.join(" + ").replace(/_/g, " ");
        ov.push({ name: "d_signal", groupId: "drishti", lock: true, extendData: { text: name, kind: m.kind },
          points: [{ timestamp: t, value: m.kind === "bull" ? b.low : b.high }] });
        if (m.kind === "bull" && m.stop !== null) {
          const end = d.bars[Math.min(i + STOP_BARS, d.bars.length - 1)];
          ov.push({ name: "d_stop", groupId: "drishti", lock: true, extendData: `stop ${m.stop.toFixed(2)}`,
            points: [{ timestamp: t, value: m.stop }, { timestamp: ts(end.time), value: m.stop }] });
        }
      });
    }
    if (ov.length) c.createOverlay(ov);
  }, [d, layers, showZones, levelsKey]);  // eslint-disable-line react-hooks/exhaustive-deps

  // indicators: values fetched per instance from the API, drawn by the catalog templates
  const indKey = JSON.stringify(inds);
  useEffect(() => {
    const c = chart.current;
    if (!c || !d) return;
    let dead = false;
    c.getIndicators().filter((i) => !inds.some((x) => x.id === i.id)).forEach((i) => c.removeIndicator({ id: i.id }));
    inds.forEach(async (inst) => {
      const spec = CATALOG[inst.kind];
      const exists = c.getIndicators({ id: inst.id }).length > 0;
      if (spec.intraday && d.tf === "1d") { if (exists) c.removeIndicator({ id: inst.id }); return; }
      if (inst.kind === "volume") {
        if (!exists) c.createIndicator({ name: "VOL", id: inst.id, calcParams: [], shortName: "Volume" });
        return;
      }
      const p = { ...spec.defaults, ...inst.params };
      let r: { series: Record<string, Pt[]> };
      try { r = await get(`/api/chart2/indicator?${qs({ key: d.key, tf: d.tf, kind: inst.kind, ...p })}`); } catch { return; }
      if (dead) return;
      const m: Ext["m"] = {};
      Object.entries(r.series).forEach(([name, pts]) => pts.forEach((pt) => { (m[ts(pt.time)] ??= {})[name] = pt.value; }));
      const ext: Ext = { m, color: inst.color };
      const calcParams = spec.params.map((k) => p[k] as number);
      const name = TEMPLATE[inst.kind as Exclude<Kind, "volume">];
      if (exists) c.overrideIndicator({ name, id: inst.id, calcParams, extendData: ext });
      else c.createIndicator({ name, id: inst.id, calcParams, extendData: ext, ...(spec.pane === "main" ? { paneId: "candle_pane" } : {}) }, spec.pane === "main");
    });
    return () => { dead = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d, indKey, indTick]);
  useEffect(() => { if (ready) lsSet(LS.ind, inds); }, [indKey, ready]); // eslint-disable-line react-hooks/exhaustive-deps

  // live 1m/5m: candles from the WebSocket every second (no page refresh); indicator values
  // refresh every 10 s. Heikin Ashi candles come from the API, so they refresh every 10 s instead.
  useEffect(() => {
    if (!d || !isLive(d.tf)) return;
    const close = d.heikin_ashi ? () => {} : openLive((msg) => {
      const lastT = bars.current.length ? bars.current[bars.current.length - 1].timestamp : 0;
      const fresh = (msg.candles ?? []).filter((x: LiveCandle) => x.time * 1000 >= lastT);
      fresh.forEach((x) => {
        const k: KLineData = {
          timestamp: x.time * 1000,
          open: x.open,
          high: x.high,
          low: x.low,
          close: x.close,
          volume: x.volume,
          ...(x.vwap != null ? { vwap: x.vwap } : {}),
        } as any;
        const last = bars.current[bars.current.length - 1];
        if (last && last.timestamp === k.timestamp) {
          bars.current[bars.current.length - 1] = k;
        } else if (!last || k.timestamp > last.timestamp) {
          bars.current.push(k);
        }
        liveCb.current?.(k);
      });
      if (fresh.length) setTick((t) => t + 1);
    }, d.key, d.tf === "5m" ? 5 : 1);
    const iv = setInterval(() => { if (d.heikin_ashi) load(); else setIndTick((t) => t + 1); }, 10000);
    return () => { close(); clearInterval(iv); };
  }, [d, load]);

  // 1d / 1h / 15m: no WebSocket candles, so during market hours (Mon-Fri 09:15-15:40 IST) the
  // bars are re-fetched every 60 s and only the newest candles are updated or appended (no reset:
  // zoom, scroll and drawings stay). User request 2026-11-12.
  useEffect(() => {
    if (!d || isLive(d.tf)) return;
    const marketOpen = () => {
      const p = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(new Date());
      const g = (t: string) => p.find((x) => x.type === t)?.value ?? "";
      const m = parseInt(g("hour")) * 60 + parseInt(g("minute"));
      return !["Sat", "Sun"].includes(g("weekday")) && m >= 9 * 60 + 15 && m <= 15 * 60 + 40;
    };
    const iv = setInterval(async () => {
      if (!marketOpen() || document.hidden) return;
      try {
        const x = await get<BarsData>(`/api/chart2/bars?${qs({ key: d.key, tf: d.tf, ha })}`);
        const lastT = bars.current.length ? bars.current[bars.current.length - 1].timestamp : 0;
        const fresh = x.bars.filter((b) => ts(b.time) >= lastT);
        fresh.forEach((b) => {
          const k: KLineData = { timestamp: ts(b.time), open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume };
          const last = bars.current[bars.current.length - 1];
          if (last && last.timestamp === k.timestamp) bars.current[bars.current.length - 1] = k;
          else if (!last || k.timestamp > last.timestamp) bars.current.push(k);
          liveCb.current?.(k);
        });
        if (fresh.length) { setTick((t) => t + 1); setIndTick((t) => t + 1); }
      } catch { /* keep the chart as it is; the next minute retries */ }
    }, 60000);
    return () => clearInterval(iv);
  }, [d?.key, d?.tf, ha]); // eslint-disable-line react-hooks/exhaustive-deps

  // user drawings: snapshot, save, undo/redo
  const snapshot = useCallback((): Saved[] => (chart.current?.getOverlays({ groupId: USER }) ?? [])
    .filter((o) => o.points.length >= o.totalStep - 1)
    .map((o) => ({
      name: o.name,
      points: o.points.map((p) => ({ timestamp: p.timestamp, dataIndex: p.dataIndex, value: p.value })),
      extendData: o.extendData,
      lock: o.lock,
    })), []);
  const commit = useCallback(() => {
    if (mute.current || !d) return;
    const s = snapshot();
    saveDrawings(d.key, d.tf, s); hist.current.push(s);
    setHstate({ u: hist.current.canUndo, r: hist.current.canRedo });
  }, [d, snapshot]);
  const events = useCallback((): Partial<Overlay> => ({
    onDrawEnd: (e) => {
      setPending(null); chart.current?.setScrollEnabled(true);
      if (e.overlay.name === "d_note") {
        const t = window.prompt("Note text", String(e.overlay.extendData ?? "")) ?? "";
        chart.current?.overrideOverlay({ id: e.overlay.id, extendData: t || "note" });
      }
      setTimeout(commit, 0);
    },
    onPressedMoveEnd: () => { setTimeout(commit, 0); },
    onRemoved: () => { setTimeout(commit, 0); },
    onSelected: (e) => { setSel(e.overlay.id); },
    onDeselected: () => { setSel(null); },
    onDoubleClick: (e) => {
      if (e.overlay.name !== "d_note") return;
      const t = window.prompt("Note text", String(e.overlay.extendData ?? ""));
      if (t !== null) { chart.current?.overrideOverlay({ id: e.overlay.id, extendData: t || "note" }); setTimeout(commit, 0); }
    },
  }), [commit]);
  const restore = useCallback((s: Saved[]) => {
    const c = chart.current;
    if (!c) return;
    mute.current = true;
    c.removeOverlay({ groupId: USER });
    if (s.length) c.createOverlay(s.map((x) => ({ ...x, groupId: USER, mode: magnet, lock: locked || x.lock, ...events() }) as OverlayCreate));
    setTimeout(() => { mute.current = false; }, 0);
  }, [events, magnet, locked]);
  useEffect(() => {  // a new stock or timeframe: its own drawings and history
    if (!d) return;
    const s = loadDrawings(d.key, d.tf);
    hist.current = new History(s);
    setHstate({ u: false, r: false });
    restore(s);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d?.key, d?.tf, d && bars.current.length > 0]);
  const cancelDraw = useCallback(() => {
    const p = pendingRef.current;
    if (!p) return;
    mute.current = true; chart.current?.removeOverlay({ id: p.id }); mute.current = false;
    setPending(null); chart.current?.setScrollEnabled(true);
  }, []);
  const draw = useCallback((name: string) => {
    const c = chart.current;
    if (!c) return;
    cancelDraw();
    const id = c.createOverlay({ name, groupId: USER, mode: magnet, lock: locked, ...events() } as OverlayCreate);
    if (typeof id === "string") { setPending({ id, name }); c.setScrollEnabled(false); }   // no panning while drawing
  }, [magnet, locked, events, cancelDraw]);

  // drag-to-draw (TradingView habit): KLineChart adds a point only on a click (press and release
  // at the same spot); a press-drag-release while drawing becomes a click at the press point and
  // a click at the release point
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    let start: { x: number; y: number; target: EventTarget | null } | null = null;
    let synth = false;
    const fire = (t: EventTarget | null, type: string, x: number, y: number) =>
      t?.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, buttons: type === "mousedown" ? 1 : 0, view: window }));
    const onDown = (e: MouseEvent) => { if (!synth && pendingRef.current && e.button === 0) start = { x: e.clientX, y: e.clientY, target: e.target }; };
    const onUp = (e: MouseEvent) => {
      const s0 = start; start = null;
      if (synth || !s0 || !pendingRef.current || Math.abs(e.clientX - s0.x) + Math.abs(e.clientY - s0.y) < 8) return;
      synth = true;
      setTimeout(() => {
        fire(s0.target, "mousedown", s0.x, s0.y); fire(s0.target, "mouseup", s0.x, s0.y);
        fire(s0.target, "mousemove", e.clientX, e.clientY);
        if (pendingRef.current) { fire(s0.target, "mousedown", e.clientX, e.clientY); fire(s0.target, "mouseup", e.clientX, e.clientY); }
        synth = false;
      }, 0);
    };
    el.addEventListener("mousedown", onDown, true);
    window.addEventListener("mouseup", onUp, true);
    return () => { el.removeEventListener("mousedown", onDown, true); window.removeEventListener("mouseup", onUp, true); };
  }, []);
  const undo = () => { const s = hist.current.undo(); if (s && d) { restore(s); saveDrawings(d.key, d.tf, s); } setHstate({ u: hist.current.canUndo, r: hist.current.canRedo }); };
  const redo = () => { const s = hist.current.redo(); if (s && d) { restore(s); saveDrawings(d.key, d.tf, s); } setHstate({ u: hist.current.canUndo, r: hist.current.canRedo }); };
  const clearAll = () => {
    if (!snapshot().length || !window.confirm("Delete all drawings on this chart?")) return;
    mute.current = true; chart.current?.removeOverlay({ groupId: USER }); mute.current = false; commit();
  };
  useEffect(() => {
    lsSet(LS.magnet, magnet);
    chart.current?.getOverlays({ groupId: USER }).forEach((o) => chart.current?.overrideOverlay({ id: o.id, mode: magnet, lock: locked }));
  }, [magnet, locked]);

  // keyboard shortcuts
  const latest = useRef({ draw, undo, redo, sel, cancelDraw });
  latest.current = { draw, undo, redo, sel, cancelDraw };
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA")) return;
      const L = latest.current;
      if (e.altKey && !e.ctrlKey && !e.metaKey) {
        const tool = TOOLS.find((x) => x.key && e.code === `Key${x.key.toUpperCase()}`);
        if (tool) { e.preventDefault(); L.draw(tool.name); }
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.code === "KeyZ") { e.preventDefault(); if (e.shiftKey) L.redo(); else L.undo(); return; }
      if ((e.ctrlKey || e.metaKey) && e.code === "KeyY") { e.preventDefault(); L.redo(); return; }
      if (e.key === "Escape" && pendingRef.current) { L.cancelDraw(); return; }
      if ((e.key === "Delete" || e.key === "Backspace") && L.sel) {
        e.preventDefault();
        chart.current?.removeOverlay({ id: L.sel }); setSel(null);
      }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);

  // top-bar actions
  const shot = () => {
    const c = chart.current;
    if (!c || !d) return;
    const a = document.createElement("a");
    a.href = c.getConvertPictureUrl(true, "png", theme === "dark" ? "#0f1115" : "#ffffff");
    a.download = `${d.symbol ?? "chart"}_${d.tf}.png`;
    a.click();
  };
  const fullscreen = () => { if (document.fullscreenElement) document.exitFullscreen(); else wrap.current?.requestFullscreen(); };
  const setType = (t: CType) => { setCtype(t); lsSet(LS.ctype, t); setMenu(""); };
  const setScaleP = (s: Scale) => { setScale(s); lsSet(LS.scale, s); };
  const setLayersP = (l: Layers) => { setLayers(l); lsSet(LS.layers, l); };
  const setThemeP = (t: "light" | "dark") => {
    setTheme(t);
    try { localStorage.setItem(LS.theme, t); } catch { /* storage blocked */ }   // plain string, as ThemeToggle
    document.documentElement.setAttribute("data-theme", t);
    window.dispatchEvent(new CustomEvent("drishti:theme", { detail: t }));
  };

  useEffect(() => { if (pinned !== null && (!hover || hover.i !== pinned)) setPinned(null); }, [hover, pinned]);

  // legend: OHLC + change of the hovered bar, else the last bar, from the chart's own data (so it
  // follows the live candles)
  const dl: KLineData[] = d ? (chart.current?.getDataList() ?? []) : [];
  const li = hover && hover.i < dl.length ? hover.i : dl.length - 1;
  const cardOpen = pinned !== null && hover !== null && hover.i === pinned;
  const lb = li >= 0 ? dl[li] : null;
  const prev = li > 0 ? dl[li - 1] : undefined;
  const chg = lb && prev ? lb.close - prev.close : null;
  const when = lb ? new Date(lb.timestamp + 19800e3).toISOString().slice(0, d?.tf === "1d" ? 10 : 16).replace("T", " ") : "";
  const tvUrl = d?.symbol ? `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(`NSE:${d.symbol}`)}` : null;
  const live = isLive(tf);

  return (
    <div ref={wrap} className="kc" data-theme={theme}>
      <div className="kc-top">
        <SymbolSearch current={d?.symbol ?? instrumentKey} tf={tf} />
        <div className="kc-group" role="group" aria-label="Timeframe">
          {TFS.map((t) => <button key={t} className={t === tf ? "sel" : ""} aria-pressed={t === tf} onClick={() => setTf(t)}>{t}</button>)}
        </div>
        <div className="kc-menu">
          <button onClick={() => setMenu(menu === "type" ? "" : "type")} title="Chart type">{CTYPES.find((c) => c[0] === ctype)?.[1]} ▾</button>
          {menu === "type" && <div className="kc-pop">{CTYPES.map(([v, l]) => <button key={v} className={v === ctype ? "sel" : ""} onClick={() => setType(v)}>{l}</button>)}</div>}
        </div>
        <button onClick={() => setMenu(menu === "ind" ? "" : "ind")}>ƒx Indicators</button>
        <button onClick={() => setMenu(menu === "settings" ? "" : "settings")} title="Settings">⚙ Settings</button>
        <button onClick={shot} title="Download a PNG of the chart">📷</button>
        <button onClick={fullscreen} title="Fullscreen (Esc to exit)">⛶</button>
        {tvUrl && <a className="kc-tv" href={tvUrl} target="_blank" rel="noopener noreferrer">Open in TradingView ↗</a>}
        {live && <span className="pill warn">{LIVE_LABEL}</span>}
      </div>
      {menu === "ind" && <IndicatorMenu inds={inds} setInds={setInds} tf={tf} close={() => setMenu("")} />}
      {menu === "settings" && (
        <div className="kc-panel">
          <b>Settings</b>
          <label className="tog">Price scale <select value={scale} onChange={(e) => setScaleP(e.target.value as Scale)}><option value="normal">Regular</option><option value="logarithm">Log</option><option value="percentage">Percent</option></select></label>
          <label className="tog">Theme <select value={theme} onChange={(e) => setThemeP(e.target.value as "light" | "dark")}><option value="light">Light</option><option value="dark">Dark</option></select></label>
          <b>Drishti layers</b>
          {levels
            ? <label className="tog"><input type="checkbox" checked={todayZ} onChange={(e) => setTodayZ(e.target.checked)} />Today&apos;s zones (not the alert&apos;s)</label>
            : <label className="tog"><input type="checkbox" checked={layers.zones} onChange={(e) => setLayersP({ ...layers, zones: e.target.checked })} />S/R zones</label>}
          <label className="tog">Candlestick patterns (context) <select value={layers.patterns} onChange={(e) => setLayersP({ ...layers, patterns: e.target.value as Layers["patterns"] })}><option value="off">off</option><option value="bullish">3 trigger patterns</option><option value="all">all 26</option></select></label>
          <label className="tog"><input type="checkbox" checked={layers.trend} onChange={(e) => setLayersP({ ...layers, trend: e.target.checked })} />Trend badges</label>
          <label className="tog"><input type="checkbox" checked={layers.signals} onChange={(e) => setLayersP({ ...layers, signals: e.target.checked })} />Confirmed-pattern tags (next bar confirmed, close ≥ EMA20 or at support) + pattern-bar low</label>
          <span className="muted small">{LEGEND} Pattern - NOT a prediction; not used by drishti-swing-v1.</span>
          <button onClick={() => setMenu("")}>Close</button>
        </div>
      )}
      {layers.trend && d && <TrendStrip d={d} live={live} />}
      <div className="kc-mknote kc-pit" role="note">
        {levels && <span>Point in time: lines and green bands are the alert&apos;s saved levels{levels.zones.length ? "" : " (this alert has no saved zones: R1 / R2 lines only)"}.</span>}
        {levels && <label className="tog"><input type="checkbox" checked={todayZ} onChange={(e) => setTodayZ(e.target.checked)} />Today&apos;s zones (not the alert&apos;s)</label>}
        <label className="tog"><input type="checkbox" checked={layers.patterns !== "off"} onChange={(e) => setLayersP({ ...layers, patterns: e.target.checked ? "bullish" : "off" })} />Candlestick patterns (context)</label>
        {(layers.patterns !== "off" || layers.signals) && <span className="kc-legend-line">{LEGEND} Pattern - NOT a prediction; not used by drishti-swing-v1. {levels ? null : <a href="/scanner">Scanner alerts</a>}</span>}
      </div>
      <div className="kc-body">
        <div className="kc-tools" role="toolbar" aria-label="Drawing tools">
          {TOOLS.map((t) => <button key={t.name} title={t.label} className={pending?.name === t.name ? "sel" : ""} aria-pressed={pending?.name === t.name}
            onClick={() => (pending?.name === t.name ? cancelDraw() : draw(t.name))}>{t.icon}</button>)}
          <hr />
          <button title={`Magnet: ${magnet === "normal" ? "off" : magnet === "weak_magnet" ? "weak" : "strong"}`} className={magnet !== "normal" ? "sel" : ""}
            onClick={() => setMagnet(magnet === "normal" ? "weak_magnet" : magnet === "weak_magnet" ? "strong_magnet" : "normal")}>🧲</button>
          <button title={locked ? "Unlock drawings" : "Lock drawings"} className={locked ? "sel" : ""} onClick={() => setLocked(!locked)}>{locked ? "🔒" : "🔓"}</button>
          <button title="Undo (Ctrl+Z)" disabled={!hstate.u} onClick={undo}>↶</button>
          <button title="Redo (Ctrl+Y)" disabled={!hstate.r} onClick={redo}>↷</button>
          <button title="Delete selected (Del)" disabled={!sel} onClick={() => { if (sel) chart.current?.removeOverlay({ id: sel }); setSel(null); }}>⌫</button>
          <button title="Delete all drawings" onClick={clearAll}>🗑</button>
        </div>
        <div className="kc-chart">
          <div ref={box} className="kc-canvas" />
          {lb && d && (
            <div className="kc-legend">
              <b>{d.symbol} · {d.tf}{d.heikin_ashi ? " · Heikin Ashi" : ""}</b> <span className="muted">{when}</span>{" "}
              O <b>{fmt(lb.open)}</b> H <b>{fmt(lb.high)}</b> L <b>{fmt(lb.low)}</b> C <b>{fmt(lb.close)}</b>{" "}
              <span style={{ color: (chg ?? 0) >= 0 ? "var(--up)" : "var(--down)" }}>{chg === null || !prev ? "" : `${chg >= 0 ? "+" : ""}${fmt(chg)} (${fmt((100 * chg) / prev.close)}%)`}</span>{" "}
              <span className="muted">Vol {(lb.volume ?? 0).toLocaleString("en-IN", { maximumFractionDigits: 0 })}</span>
              {(() => { const f = d.candles.find((m) => ts(m.time) === lb.timestamp)?.flags; return f?.length ? <span className="kc-legend-pat"> pattern: {f.join(", ")}</span> : null; })()}
            </div>
          )}
          {cardOpen && hover && lb && d && <BarCard d={d} k={lb} prev={prev} when={when} x={hover.x} width={box.current?.clientWidth ?? 0} live={live}
            inds={chart.current?.getIndicators() ?? []} i={li} />}
          {live && <span className="pill warn kc-livetag">{LIVE_LABEL}</span>}
          {pending && (
            <div className="kc-hint">
              {TOOLS.find((t) => t.name === pending.name)?.label.replace(/ \(.*\)$/, "")}: click the start point, then the end point (or drag)
              {pending.name === "parallelStraightLine" ? ", then click once more for the channel width" : ""} · Esc cancels
            </div>
          )}
          {err && <p className="muted kc-err">{err}</p>}
          {!d && !err && <p className="muted kc-err">Loading…</p>}
        </div>
      </div>
    </div>
  );
}

// floating details of the bar under the mouse (TradingView-style data window): values are the
// chart's own data and the API's indicator results, only formatted here
function BarCard({ d, k, prev, when, x, width, live, inds, i }: {
  d: BarsData; k: KLineData; prev?: KLineData; when: string; x: number; width: number; live: boolean; inds: Indicator[]; i: number;
}) {
  const chg = prev ? k.close - prev.close : null;
  const pats = d.candles.find((c) => ts(c.time) === k.timestamp)?.flags ?? [];
  const sb = d.bars.find((b) => ts(b.time) === k.timestamp);   // undefined for a live bar newer than the API data
  const dir = k.close > k.open ? "bull" : k.close < k.open ? "bear" : "flat";
  const DIR = { bull: ["Bullish (green)", "var(--up)"], bear: ["Bearish (red)", "var(--down)"], flat: ["Flat (open = close)", "var(--muted)"] } as const;
  const W = 250;
  const left = x + 18 + W > width ? Math.max(4, x - W - 18) : x + 18;
  const rows: [string, string][] = [];
  inds.forEach((ind) => {
    const r = (ind.result as Record<string, number | undefined>[])[i] ?? {};
    const vals = ind.figures.filter((f) => f.key !== "open" && f.key !== "close").map((f) => r[f.key]).filter((v): v is number => typeof v === "number");
    if (ind.name === "VOL" || !vals.length) return;
    const name = `${ind.shortName}${ind.calcParams.length ? ` ${ind.calcParams.join(", ")}` : ""}`;
    const figs = ind.figures.filter((f) => typeof r[f.key] === "number");
    rows.push([name, figs.map((f) => `${figs.length > 1 && f.title ? f.title.replace(/: $/, "") + " " : ""}${fmt(r[f.key] as number)}`).join(" · ")]);
  });
  return (
    <div className="kc-card" style={{ left, width: W }}>
      {live && <div className="pill warn" style={{ marginBottom: 4 }}>{LIVE_LABEL}</div>}
      <b>{d.symbol} · {d.tf}{d.heikin_ashi ? " · Heikin Ashi" : ""}</b>
      <div className="muted">{when}{d.tf === "1d" ? "" : " IST"}</div>
      <div style={{ color: DIR[dir][1], fontWeight: 700, marginTop: 2 }}>{DIR[dir][0]}</div>
      <table><tbody>
        <tr><td>Open</td><td>{fmt(k.open)}</td></tr>
        <tr><td>High</td><td>{fmt(k.high)}</td></tr>
        <tr><td>Low</td><td>{fmt(k.low)}</td></tr>
        <tr><td>Close</td><td>{fmt(k.close)}</td></tr>
        <tr><td>Change</td><td style={{ color: (chg ?? 0) >= 0 ? "var(--up)" : "var(--down)" }}>{chg === null || !prev ? "–" : `${chg >= 0 ? "+" : ""}${fmt(chg)} (${fmt((100 * chg) / prev.close)}%)`}</td></tr>
        <tr><td>Range</td><td>{fmt(k.high - k.low)}</td></tr>
        <tr><td>Volume</td><td>{(k.volume ?? 0).toLocaleString("en-IN", { maximumFractionDigits: 0 })}</td></tr>
        {rows.map(([a, v]) => <tr key={a}><td>{a}</td><td>{v}</td></tr>)}
        {pats.length > 0 && <tr><td>Patterns</td><td>{pats.join(", ").replace(/_/g, " ")}<div className="muted small">context only · NOT a prediction</div></td></tr>}
        {dir !== "flat" && <>
          <tr><td title={dir === "bull" ? "did the next bar close above this bar's high?" : "did the next bar close below this bar's low?"}>Next bar (hindsight)</td>
            <td>{sb?.confirm === "confirmed" ? `closed ${dir === "bull" ? "above this high" : "below this low"} (confirmed next bar)` : sb?.confirm === "not confirmed" ? "not confirmed" : "pending"}</td></tr>
          {(sb?.confirm === "confirmed") && <tr><td title="did the bar after that close further the same way?">Bar after (hindsight)</td>
            <td>{sb?.follow === "follow-through" ? `closed ${dir === "bull" ? "higher" : "lower"} again` : sb?.follow === "no follow-through" ? "did not continue" : "pending"}</td></tr>}
        </>}
      </tbody></table>
      <div className="muted small" style={{ marginTop: 4 }}>Next bar / bar after: what later bars did (hindsight, not a signal). Patterns: NOT a prediction; not used by drishti-swing-v1.</div>
    </div>
  );
}

// spec trend (levels.trend) with its reasons, and the MA-only trend; decision support only
function TrendStrip({ d, live }: { d: BarsData; live: boolean }) {
  const [open, setOpen] = useState(false);
  const t = d.trend_detail;
  const cls = (v: string) => (v === "UP" ? "on" : v === "DOWN" ? "off" : "");
  return (
    <div className="kc-status">
      <span className="kc-why" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
        <button className={`pill ${cls(t.class)}`} onClick={() => setOpen(!open)} aria-expanded={open}
          title="Spec section 3 trend rule (levels.trend): click or hover for the reason">
          Spec trend {d.tf}: <b>{t.class}</b> ⓘ
        </button>
        {open && (
          <div className="kc-why-pop" role="tooltip">
            <b>Spec trend {d.tf}: {t.class}</b>
            <div className="muted small">UP needs all three; DOWN needs the mirror (spec §3, unchanged).</div>
            <ul>{t.conditions.map((c) => <li key={c.name} className={c.ok ? "ok" : "no"}>{c.text}</li>)}</ul>
            <div className="muted small">{t.note}{live ? " · LIVE VIEW" : ""}</div>
          </div>
        )}
      </span>
      <span className={`pill ${cls(t.ma_trend)}`} title="close vs SMA50 vs SMA200 only (more responsive than the spec rule); not used by drishti-swing-v1">
        MA trend: <b>{t.ma_trend}</b>
      </span>
      <span className="muted kc-status-note">not used by drishti-swing-v1{live ? " · LIVE VIEW" : ""}</span>
    </div>
  );
}

function IndicatorMenu({ inds, setInds, tf, close }: { inds: Instance[]; setInds: (x: Instance[]) => void; tf: Tf; close: () => void }) {
  const [q, setQ] = useState("");
  const kinds = useMemo(() => (Object.keys(CATALOG) as Kind[]).filter((k) => CATALOG[k].search.includes(q.trim().toLowerCase())), [q]);
  const add = (k: Kind) => {
    const n = inds.filter((i) => i.kind === k).length;
    if (k === "volume" && n) return;
    setInds([...inds, { id: `${k}-${Date.now().toString(36)}`, kind: k, params: { ...CATALOG[k].defaults }, color: PALETTE[inds.length % PALETTE.length] }]);
  };
  const upd = (id: string, p: Params) => setInds(inds.map((i) => (i.id === id ? { ...i, params: { ...i.params, ...p } } : i)));
  return (
    <div className="kc-panel kc-ind">
      <div className="row"><b>Indicators</b><input autoFocus placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search indicators" /><button onClick={close}>Close</button></div>
      <div className="kc-ind-list">
        {kinds.map((k) => (
          <button key={k} onClick={() => add(k)} disabled={!!CATALOG[k].intraday && tf === "1d"} title={CATALOG[k].intraday && tf === "1d" ? "intraday only" : "add"}>
            + {CATALOG[k].label}{CATALOG[k].intraday ? " (intraday)" : ""}
          </button>
        ))}
      </div>
      <b>On the chart</b>
      {inds.length === 0 && <span className="muted">none</span>}
      {inds.map((i) => (
        <div key={i.id} className="row kc-ind-row">
          <span style={{ color: i.color, minWidth: 130 }}>{title(i)}</span>
          {CATALOG[i.kind].params.map((p) => (
            <label key={p} className="tog">{PARAM_LABEL[p]}
              <input type="number" style={{ width: 64 }} step={p === "k" || p === "mult" ? 0.1 : 1} min={p === "k" || p === "mult" ? 0.1 : 1}
                defaultValue={i.params[p] ?? CATALOG[i.kind].defaults[p]}
                onBlur={(e) => { const v = Number(e.target.value); if (v > 0) upd(i.id, { [p]: v }); }}
                onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
            </label>
          ))}
          {CATALOG[i.kind].pane === "main" && <input type="color" value={i.color ?? "#3a86ff"} aria-label="colour" onChange={(e) => setInds(inds.map((x) => (x.id === i.id ? { ...x, color: e.target.value } : x)))} />}
          <button onClick={() => setInds(inds.filter((x) => x.id !== i.id))} aria-label={`remove ${title(i)}`}>✕</button>
        </div>
      ))}
      <span className="muted small">Values from drishti.features (same numbers as the backend; Bollinger, MACD, VWAP, Supertrend: interim stubs until Track B). MACD histogram = MACD − signal.</span>
      <button onClick={() => setInds(DEFAULT_SET)}>Reset to default</button>
    </div>
  );
}
