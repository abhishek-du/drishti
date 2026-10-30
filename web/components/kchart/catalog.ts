// Indicators menu of the chart. Values come from /api/chart2/indicator (drishti.features).
// The templates below draw what the API returned, and for live forming bars (not yet in the API
// cache) dynamically compute the newest bar so the line and tooltips update in real time with the ticks.
import { IndicatorFigure, IndicatorTemplate, KLineData, registerIndicator } from "klinecharts";

export type Kind = "ema" | "sma" | "vwap" | "bb" | "rsi" | "macd" | "atr" | "supertrend" | "volume";
export type Params = { n?: number; k?: number; fast?: number; slow?: number; signal?: number; mult?: number };
export type Instance = { id: string; kind: Kind; params: Params; color?: string };
type Spec = { label: string; search: string; pane: "main" | "sub"; params: (keyof Params)[]; defaults: Params; intraday?: boolean };

export const CATALOG: Record<Kind, Spec> = {
  ema: { label: "EMA", search: "ema exponential moving average", pane: "main", params: ["n"], defaults: { n: 20 } },
  sma: { label: "SMA", search: "sma simple moving average ma", pane: "main", params: ["n"], defaults: { n: 50 } },
  vwap: { label: "VWAP", search: "vwap volume weighted average price", pane: "main", params: [], defaults: {}, intraday: true },
  bb: { label: "Bollinger Bands", search: "bollinger bands bb", pane: "main", params: ["n", "k"], defaults: { n: 20, k: 2 } },
  supertrend: { label: "Supertrend", search: "supertrend st", pane: "main", params: ["n", "mult"], defaults: { n: 10, mult: 3 } },
  volume: { label: "Volume", search: "volume vol", pane: "sub", params: [], defaults: {} },
  rsi: { label: "RSI", search: "rsi relative strength index", pane: "sub", params: ["n"], defaults: { n: 14 } },
  macd: { label: "MACD", search: "macd moving average convergence divergence", pane: "sub", params: ["fast", "slow", "signal"], defaults: { fast: 12, slow: 26, signal: 9 } },
  atr: { label: "ATR", search: "atr average true range volatility", pane: "sub", params: ["n"], defaults: { n: 14 } },
};
export const PARAM_LABEL: Record<keyof Params, string> = { n: "Length", k: "StdDev", fast: "Fast", slow: "Slow", signal: "Signal", mult: "Multiplier" };

export const DEFAULT_SET: Instance[] = [
  { id: "ema20", kind: "ema", params: { n: 20 }, color: "#e0a100" },
  { id: "sma50", kind: "sma", params: { n: 50 }, color: "#3a86ff" },
  { id: "sma200", kind: "sma", params: { n: 200 }, color: "#9d4edd" },
  { id: "vwap", kind: "vwap", params: {}, color: "#e76f51" },
  { id: "volume", kind: "volume", params: {} },
  { id: "rsi14", kind: "rsi", params: { n: 14 }, color: "#9d4edd" },
];
export const PALETTE = ["#e0a100", "#3a86ff", "#9d4edd", "#2a9d8f", "#e76f51", "#d6336c", "#5c940d", "#495057"];

export const paramText = (i: Instance) => CATALOG[i.kind].params.map((p) => i.params[p] ?? CATALOG[i.kind].defaults[p]).join(", ");
export const title = (i: Instance) => `${CATALOG[i.kind].label}${paramText(i) ? ` ${paramText(i)}` : ""}`;

// one template per kind; the series arrive in extendData.m: timestamp -> { series: value }
export type Ext = { m: Record<number, Record<string, number>>; color?: string };
type Row = Record<string, number>;

function calcLiveBar(name: string, p: number[], k: KLineData, i: number, dataList: KLineData[], prev?: Row): Row {
  const row: Row = {};
  if (name === "D_EMA") {
    const n = p[0] || 20;
    const alpha = 2 / (n + 1);
    row.ema = prev?.ema !== undefined ? k.close * alpha + prev.ema * (1 - alpha) : k.close;
  } else if (name === "D_SMA") {
    const n = p[0] || 50;
    const w = dataList.slice(Math.max(0, i - n + 1), i + 1);
    row.sma = w.reduce((s, b) => s + b.close, 0) / w.length;
  } else if (name === "D_VWAP") {
    const kv = (k as any).vwap;
    if (typeof kv === "number" && !isNaN(kv)) {
      row.vwap = kv;
    } else if (prev?.vwap !== undefined) {
      row.vwap = prev.vwap;
    }
  } else if (name === "D_BB") {
    const n = p[0] || 20;
    const mult = p[1] || 2;
    const w = dataList.slice(Math.max(0, i - n + 1), i + 1);
    const mid = w.reduce((s, b) => s + b.close, 0) / w.length;
    const variance = w.reduce((s, b) => s + Math.pow(b.close - mid, 2), 0) / w.length;
    const std = Math.sqrt(variance);
    row.bb_mid = mid;
    row.bb_up = mid + mult * std;
    row.bb_lo = mid - mult * std;
  } else if (name === "D_ST") {
    const n = p[0] || 10;
    const mult = p[1] || 3;
    if (prev?.st !== undefined) {
      const tr = Math.max(k.high - k.low, Math.abs(k.high - dataList[i - 1].close), Math.abs(k.low - dataList[i - 1].close));
      const prevAtr = (prev as any)._atr ?? (k.high - k.low);
      const atr = (prevAtr * (n - 1) + tr) / n;
      (row as any)._atr = atr;
      const hl2 = (k.high + k.low) / 2;
      const up = hl2 - mult * atr;
      const dn = hl2 + mult * atr;
      let dir = prev.st_dir ?? 1;
      if (dir === -1 && k.close > prev.st) dir = 1;
      else if (dir === 1 && k.close < prev.st) dir = -1;
      row.st_dir = dir;
      row.st = dir === 1 ? Math.max(up, prev.st_dir === 1 ? prev.st : up) : Math.min(dn, prev.st_dir === -1 ? prev.st : dn);
    }
  } else if (name === "D_RSI") {
    const n = p[0] || 14;
    if (prev?.rsi !== undefined && i > 0) {
      const diff = k.close - dataList[i - 1].close;
      const gain = diff > 0 ? diff : 0;
      const loss = diff < 0 ? -diff : 0;
      const rsPrev = prev.rsi / Math.max(0.001, 100 - prev.rsi);
      const avgLossPrev = (k.close * 0.01) / (rsPrev + 1);
      const avgGainPrev = rsPrev * avgLossPrev;
      const avgGain = (avgGainPrev * (n - 1) + gain) / n;
      const avgLoss = (avgLossPrev * (n - 1) + loss) / n;
      const rs = avgGain / Math.max(0.0001, avgLoss);
      row.rsi = 100 - (100 / (1 + rs));
    }
  } else if (name === "D_MACD") {
    const fast = p[0] || 12;
    const slow = p[1] || 26;
    const sig = p[2] || 9;
    if (prev?.macd !== undefined && prev?.macd_signal !== undefined && i > 0) {
      const aFast = 2 / (fast + 1);
      const aSlow = 2 / (slow + 1);
      const aSig = 2 / (sig + 1);
      const macd = prev.macd + (k.close - dataList[i - 1].close) * (aFast - aSlow);
      const signal = macd * aSig + prev.macd_signal * (1 - aSig);
      row.macd = macd;
      row.macd_signal = signal;
      row.macd_hist = macd - signal;
    }
  } else if (name === "D_ATR") {
    const n = p[0] || 14;
    if (prev?.atr !== undefined && i > 0) {
      const tr = Math.max(k.high - k.low, Math.abs(k.high - dataList[i - 1].close), Math.abs(k.low - dataList[i - 1].close));
      row.atr = (prev.atr * (n - 1) + tr) / n;
    }
  }
  return row;
}

const fromApi = (dataList: KLineData[], ind: { extendData: Ext; calcParams?: number[]; name: string }) => {
  const m = ind.extendData?.m;
  const p = (ind.calcParams ?? []) as number[];
  const out: Row[] = new Array(dataList.length);
  for (let i = 0; i < dataList.length; i++) {
    const k = dataList[i];
    const hit = m?.[k.timestamp];
    if (hit && Object.keys(hit).length > 0) {
      out[i] = hit;
    } else {
      out[i] = calcLiveBar(ind.name, p, k, i, dataList, i > 0 ? out[i - 1] : undefined);
    }
  }
  return out;
};
const line = (key: string, t: string, color?: (e: Ext) => string | undefined): IndicatorFigure<Row> => ({
  key, title: `${t}: `, type: "line",
  styles: ({ indicator }) => {
    const e = indicator.extendData as Ext;
    const c = color ? color(e) : e?.color;
    return c ? { color: c } : null;
  },
});

const TEMPLATES: IndicatorTemplate<Row, number, Ext>[] = [
  { name: "D_EMA", shortName: "EMA", series: "price", precision: 2, figures: [line("ema", "EMA")], calc: fromApi },
  { name: "D_SMA", shortName: "SMA", series: "price", precision: 2, figures: [line("sma", "SMA")], calc: fromApi },
  { name: "D_VWAP", shortName: "VWAP", series: "price", precision: 2, figures: [line("vwap", "VWAP")], calc: fromApi },
  {
    name: "D_BB", shortName: "BB", series: "price", precision: 2, calc: fromApi,
    figures: [line("bb_up", "Upper"), line("bb_mid", "Basis", () => "#adb5bd"), line("bb_lo", "Lower")],
  },
  {
    name: "D_ST", shortName: "Supertrend", series: "price", precision: 2, calc: fromApi,
    figures: [{
      key: "st", title: "ST: ", type: "line",
      styles: ({ data }) => ({ color: (data.current?.st_dir ?? 1) >= 0 ? "#12805c" : "#c0362c" }),
    }],
  },
  { name: "D_RSI", shortName: "RSI", precision: 2, minValue: 0, maxValue: 100, figures: [line("rsi", "RSI")], calc: fromApi },
  {
    name: "D_MACD", shortName: "MACD", precision: 2, calc: fromApi,
    figures: [
      { key: "macd_hist", title: "Hist: ", type: "bar", baseValue: 0, styles: ({ data }) => ({ color: (data.current?.macd_hist ?? 0) >= 0 ? "#26a69a99" : "#ef535099" }) },
      line("macd", "MACD", () => "#3a86ff"), line("macd_signal", "Signal", () => "#e0a100"),
    ],
  },
  { name: "D_ATR", shortName: "ATR", precision: 2, figures: [line("atr", "ATR")], calc: fromApi },
];
export const TEMPLATE: Record<Exclude<Kind, "volume">, string> = { ema: "D_EMA", sma: "D_SMA", vwap: "D_VWAP", bb: "D_BB", supertrend: "D_ST", rsi: "D_RSI", macd: "D_MACD", atr: "D_ATR" };

let registered = false;
export function registerTemplates() {
  if (registered) return;
  registered = true;
  TEMPLATES.forEach((t) => registerIndicator(t));
}
