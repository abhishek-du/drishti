"use client";
// TradingView-style chart on KLineChart v10 (Apache 2.0). Bars, indicator values, candle flags,
// zones and the trend all come from the read-only API (drishti.features); this file only draws.
// Drawings are kept per stock and timeframe in this browser's localStorage (no DB writes).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Chart, dispose, init, KLineData, Overlay, OverlayCreate } from "klinecharts";
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
  bars: { time: string | number; open: number; high: number; low: number; close: number; volume: number; chg_pct: number | null }[];
  lines: Record<string, Pt[]>; panes: Record<string, Pt[]>;
  candles: { time: string | number; flags: string[] }[]; bullish_flags: string[];
  zones: { price: number; bottom: number; top: number; touches: number }[];
  trend: "UP" | "DOWN" | "SIDE";
  last: { ema20: number | null; sma50: number | null; vwap: number | null; rsi: number | null; atr: number | null; close: number | null };
  bias: { label: string; text: string; rules: { rule: string; state: unknown; text: string }[] };
};
type CType = "candle_solid" | "candle_stroke" | "ohlc" | "line" | "area" | "ha";
const CTYPES: [CType, string][] = [["candle_solid", "Candles"], ["candle_stroke", "Hollow candles"], ["ohlc", "Bars"], ["line", "Line"], ["area", "Area"], ["ha", "Heikin Ashi"]];
type Scale = "normal" | "logarithm" | "percentage";
type Layers = { zones: boolean; patterns: "bullish" | "all" | "off"; trend: boolean };
type Magnet = "normal" | "weak_magnet" | "strong_magnet";

export const ts = (t: string | number) => (typeof t === "number" ? t * 1000 : Date.parse(`${t}T00:00:00+05:30`));
const LS = { ind: "drishti.chart.ind.v1", ctype: "drishti.chart.ctype", scale: "drishti.chart.scale", layers: "drishti.chart.layers", theme: "drishti.theme", magnet: "drishti.chart.magnet" };
function ls<T>(k: string, d: T): T { try { const v = localStorage.getItem(k); return v ? (JSON.parse(v) as T) : d; } catch { return d; } }
function lsSet(k: string, v: unknown) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage blocked */ } }
const qs = (o: Record<string, string | number | boolean>) => Object.entries(o).map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&");

export default function KChart({ instrumentKey, tf, setTf, onData }: {
  instrumentKey: string; tf: Tf; setTf: (t: Tf) => void; onData: (d: BarsData | null) => void;
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
  const [layers, setLayers] = useState<Layers>({ zones: true, patterns: "bullish", trend: true });
  const [inds, setInds] = useState<Instance[]>(DEFAULT_SET);
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [magnet, setMagnet] = useState<Magnet>("weak_magnet");
  const [locked, setLocked] = useState(false);
  const [sel, setSel] = useState<string | null>(null);
  const [legend, setLegend] = useState<number | null>(null);
  const [menu, setMenu] = useState<"" | "ind" | "settings" | "type">("");
  const [hstate, setHstate] = useState({ u: false, r: false });
  const [ready, setReady] = useState(false);

  // preferences (per browser)
  useEffect(() => {
    setCtype(ls(LS.ctype, "candle_solid")); setScale(ls(LS.scale, "normal")); setInds(ls(LS.ind, DEFAULT_SET));
    setLayers({ zones: true, patterns: "bullish", trend: true, ...ls(LS.layers, {}) }); setMagnet(ls(LS.magnet, "weak_magnet"));
    setTheme(ls<"light" | "dark" | null>(LS.theme, null) ?? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
    setReady(true);
  }, []);
  useEffect(() => { if (ready) document.documentElement.dataset.theme = theme; }, [theme, ready]);

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
    c.subscribeAction("onCrosshairChange", (x) => {
      const i = (x as { dataIndex?: number; kLineData?: KLineData })?.kLineData ? (x as { dataIndex: number }).dataIndex : null;
      setLegend(i);
    });
    const ro = new ResizeObserver(() => c.resize());
    ro.observe(box.current);
    const fs = () => setTimeout(() => c.resize(), 50);
    document.addEventListener("fullscreenchange", fs);
    return () => { ro.disconnect(); document.removeEventListener("fullscreenchange", fs); dispose(c); chart.current = null; };
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

  // Drishti layers
  useEffect(() => {
    const c = chart.current;
    if (!c || !d || !bars.current.length) return;
    c.removeOverlay({ groupId: "drishti" });
    const t0 = bars.current[0].timestamp, t1 = bars.current[bars.current.length - 1].timestamp;
    const ov: OverlayCreate[] = [];
    if (layers.zones) d.zones.forEach((z) => ov.push({ name: "d_zone", groupId: "drishti", lock: true, extendData: `S/R zone ×${z.touches} · ${z.price.toFixed(2)}`, points: [{ timestamp: t0, value: z.top }, { timestamp: t1, value: z.bottom }] }));
    if (layers.patterns !== "off") {
      const bull = new Set(d.bullish_flags);
      const low = new Map(d.bars.map((b) => [ts(b.time), b.low]));
      d.candles.forEach((m) => {
        const fl = layers.patterns === "all" ? m.flags : m.flags.filter((f) => bull.has(f));
        if (fl.length) ov.push({ name: "d_pat", groupId: "drishti", lock: true, extendData: { text: fl.join(", "), bull: fl.some((f) => bull.has(f)) }, points: [{ timestamp: ts(m.time), value: low.get(ts(m.time)) }] });
      });
    }
    if (ov.length) c.createOverlay(ov);
  }, [d, layers]);

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
  }, [d, indKey]);
  useEffect(() => { if (ready) lsSet(LS.ind, inds); }, [indKey, ready]); // eslint-disable-line react-hooks/exhaustive-deps

  // live 1m/5m: the forming candle from the WebSocket; history and indicators refresh every 15 s
  useEffect(() => {
    if (!d || !isLive(d.tf)) return;
    const close = openLive((msg) => {
      const lastT = bars.current.length ? bars.current[bars.current.length - 1].timestamp : 0;
      (msg.candles ?? []).filter((x: LiveCandle) => x.time * 1000 >= lastT && !d.heikin_ashi).forEach((x) =>
        liveCb.current?.({ timestamp: x.time * 1000, open: x.open, high: x.high, low: x.low, close: x.close, volume: x.volume }));
    }, d.key, d.tf === "5m" ? 5 : 1);
    const iv = setInterval(() => setInds((x) => [...x]), 15000);
    return () => { close(); clearInterval(iv); };
  }, [d]);

  // user drawings: snapshot, save, undo/redo
  const snapshot = useCallback((): Saved[] => (chart.current?.getOverlays({ groupId: USER }) ?? [])
    .filter((o) => o.points.length >= o.totalStep - 1)
    .map((o) => ({ name: o.name, points: o.points.map((p) => ({ timestamp: p.timestamp, value: p.value })), extendData: o.extendData, lock: o.lock })), []);
  const commit = useCallback(() => {
    if (mute.current || !d) return;
    const s = snapshot();
    saveDrawings(d.key, d.tf, s); hist.current.push(s);
    setHstate({ u: hist.current.canUndo, r: hist.current.canRedo });
  }, [d, snapshot]);
  const events = useCallback((): Partial<Overlay> => ({
    onDrawEnd: (e) => {
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
  const draw = useCallback((name: string) => {
    chart.current?.createOverlay({ name, groupId: USER, mode: magnet, lock: locked, ...events() } as OverlayCreate);
  }, [magnet, locked, events]);
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
  const latest = useRef({ draw, undo, redo, sel });
  latest.current = { draw, undo, redo, sel };
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
  const setThemeP = (t: "light" | "dark") => { setTheme(t); lsSet(LS.theme, t); };

  // legend (OHLC + change of the hovered bar, else the last bar)
  const lb = d && d.bars.length ? d.bars[legend !== null && legend >= 0 && legend < d.bars.length ? legend : d.bars.length - 1] : null;
  const prev = d && lb ? d.bars[d.bars.indexOf(lb) - 1] : undefined;
  const chg = lb && prev ? lb.close - prev.close : null;
  const when = lb ? (typeof lb.time === "number" ? new Date((lb.time + 19800) * 1000).toISOString().slice(0, 16).replace("T", " ") : lb.time) : "";
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
          <label className="tog"><input type="checkbox" checked={layers.zones} onChange={(e) => setLayersP({ ...layers, zones: e.target.checked })} />S/R zones</label>
          <label className="tog">Candle patterns <select value={layers.patterns} onChange={(e) => setLayersP({ ...layers, patterns: e.target.value as Layers["patterns"] })}><option value="bullish">trigger flags</option><option value="all">all 26</option><option value="off">off</option></select></label>
          <label className="tog"><input type="checkbox" checked={layers.trend} onChange={(e) => setLayersP({ ...layers, trend: e.target.checked })} />Trend badges</label>
          <span className="muted small">Signals / entry / SL / target lines: later (with P3).</span>
          <button onClick={() => setMenu("")}>Close</button>
        </div>
      )}
      <div className="kc-body">
        <div className="kc-tools" role="toolbar" aria-label="Drawing tools">
          {TOOLS.map((t) => <button key={t.name} title={t.label} onClick={() => draw(t.name)}>{t.icon}</button>)}
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
              <span style={{ color: (chg ?? 0) >= 0 ? "var(--up)" : "var(--down)" }}>{chg === null ? "" : `${chg >= 0 ? "+" : ""}${fmt(chg)} (${fmt(lb.chg_pct)}%)`}</span>{" "}
              <span className="muted">Vol {lb.volume.toLocaleString("en-IN", { maximumFractionDigits: 0 })}</span>
            </div>
          )}
          {layers.trend && d && (
            <div className={`kc-trend pill ${d.trend === "UP" ? "on" : d.trend === "DOWN" ? "off" : ""}`} title="Spec section 3 trend rule">
              Trend {d.tf}: {d.trend}{live ? " · live view" : ""}
            </div>
          )}
          {live && <span className="pill warn kc-livetag">{LIVE_LABEL}</span>}
          {err && <p className="muted kc-err">{err}</p>}
          {!d && !err && <p className="muted kc-err">Loading…</p>}
        </div>
      </div>
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
