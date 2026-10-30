// Indicators menu of the chart. Every value comes from /api/chart2/indicator (drishti.features,
// or chart_stubs until Track B lands them): the templates below only draw what the API returned,
// keyed by bar timestamp. Nothing is computed here. Volume is the raw bar volume (built-in VOL
// with no moving averages).
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
const fromApi = (dataList: KLineData[], ind: { extendData: Ext }) => dataList.map((k) => ind.extendData?.m?.[k.timestamp] ?? {});
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
