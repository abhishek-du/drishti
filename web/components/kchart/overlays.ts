// Drawing tools (built-in KLineChart overlays plus a few of our own) and the Drishti layers.
import { OverlayFigure, OverlayTemplate, registerOverlay } from "klinecharts";

export type Tool = { name: string; label: string; icon: string; key?: string };
export const TOOLS: Tool[] = [
  { name: "segment", label: "Trend line (Alt+T)", icon: "╱", key: "t" },
  { name: "rayLine", label: "Ray", icon: "↗" },
  { name: "horizontalStraightLine", label: "Horizontal line (Alt+H)", icon: "―", key: "h" },
  { name: "verticalStraightLine", label: "Vertical line (Alt+V)", icon: "│", key: "v" },
  { name: "parallelStraightLine", label: "Parallel channel", icon: "⫽" },
  { name: "fibonacciLine", label: "Fibonacci retracement (Alt+F)", icon: "𝔽", key: "f" },
  { name: "d_rect", label: "Rectangle", icon: "▭" },
  { name: "d_note", label: "Text / note", icon: "T" },
  { name: "d_measure", label: "Price range / measure", icon: "⇕" },
];

type Coord = { x: number; y: number };
type Pt = { timestamp?: number; dataIndex?: number; value?: number };

const rect: OverlayTemplate = {
  name: "d_rect", totalStep: 3, needDefaultPointFigure: true, needDefaultXAxisFigure: true, needDefaultYAxisFigure: true,
  createPointFigures: ({ coordinates }) => {
    if (coordinates.length < 2) return [];
    const [a, b] = coordinates as Coord[];
    return [{ type: "rect", attrs: { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), width: Math.abs(b.x - a.x), height: Math.abs(b.y - a.y) }, styles: { style: "stroke_fill", color: "rgba(58,134,255,.12)", borderColor: "#3a86ff" } }];
  },
};

// price range: the difference between two points, in price, % and bars (display arithmetic only)
const measure: OverlayTemplate = {
  name: "d_measure", totalStep: 3, needDefaultPointFigure: true, needDefaultXAxisFigure: true, needDefaultYAxisFigure: true,
  createPointFigures: ({ coordinates, overlay, chart }) => {
    if (coordinates.length < 2) return [];
    const [a, b] = coordinates as Coord[];
    const [p, q] = overlay.points as Pt[];
    if (p?.value === undefined || q?.value === undefined) return [];
    const d = q.value - p.value, up = d >= 0;
    // bars from dataIndex if both present, else on-screen distance (handles points in empty space)
    const barSpace = chart.getBarSpace().bar || 1;
    const bars = typeof p.dataIndex === "number" && typeof q.dataIndex === "number"
      ? Math.abs(q.dataIndex - p.dataIndex)
      : Math.abs(Math.round((b.x - a.x) / barSpace));
    const color = up ? "rgba(18,128,92,.15)" : "rgba(192,54,44,.15)";
    return [
      { type: "rect", attrs: { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), width: Math.abs(b.x - a.x), height: Math.abs(b.y - a.y) }, styles: { style: "fill", color } },
      {
        type: "text", ignoreEvent: true,
        attrs: { x: (a.x + b.x) / 2, y: up ? Math.min(a.y, b.y) - 4 : Math.max(a.y, b.y) + 4, text: `${d >= 0 ? "+" : ""}${d.toFixed(2)} (${((100 * d) / p.value).toFixed(2)}%) · ${bars} bars`, align: "center", baseline: up ? "bottom" : "top" },
        styles: { color: "#fff", backgroundColor: up ? "#12805c" : "#c0362c", paddingLeft: 4, paddingRight: 4, paddingTop: 2, paddingBottom: 2, borderRadius: 3, size: 12 },
      },
    ];
  },
};

const note: OverlayTemplate = {
  name: "d_note", totalStep: 2, needDefaultPointFigure: true,
  createPointFigures: ({ coordinates, overlay }) => [{
    type: "text", attrs: { x: coordinates[0].x, y: coordinates[0].y, text: String(overlay.extendData ?? "note"), align: "left", baseline: "bottom" },
    styles: { color: "#1a1d21", backgroundColor: "#ffe066", paddingLeft: 4, paddingRight: 4, paddingTop: 2, paddingBottom: 2, borderRadius: 3, size: 12 },
  }],
};

// Drishti S/R zones, all in one overlay so the labels can be laid out together: bands across the
// whole width; a label is skipped when it would sit within LABEL_GAP px of one already drawn or
// outside the pane (extendData: [{top, bottom, label}])
const LABEL_GAP = 15;
const zones: OverlayTemplate = {
  name: "d_zones", totalStep: 2, lock: true, needDefaultPointFigure: false,
  createPointFigures: ({ bounding, overlay, yAxis }) => {
    if (!yAxis) return [];
    const zs = (overlay.extendData ?? []) as { top: number; bottom: number; label: string }[];
    const bands = zs.map((z) => {
      const a = yAxis.convertToPixel(z.top), b = yAxis.convertToPixel(z.bottom);
      return { y0: Math.min(a, b), y1: Math.max(a, b), label: z.label };
    });
    const figs: OverlayFigure[] = bands.map((z) => ({ type: "rect", ignoreEvent: true,
      attrs: { x: 0, y: z.y0, width: bounding.width, height: Math.max(1, z.y1 - z.y0) }, styles: { style: "fill", color: "rgba(120,120,140,.14)" } }));
    let last = -Infinity;
    [...bands].sort((p, q) => p.y0 - q.y0).forEach((z) => {
      if (z.y0 < 14 || z.y0 > bounding.height - 4 || z.y0 - last < LABEL_GAP) return;
      last = z.y0;
      figs.push({ type: "text", ignoreEvent: true, attrs: { x: 4, y: z.y0, text: z.label, align: "left", baseline: "bottom" },
        styles: { color: "#8a94a6", backgroundColor: "rgba(15,17,21,.55)", size: 10, paddingLeft: 3, paddingRight: 3, paddingTop: 1, paddingBottom: 1, borderRadius: 2 } });
    });
    return figs;
  },
};

// Drishti candle-pattern marker under a bar (point = the bar's low; extendData = {bull}): a small
// triangle only, so markers never collide; the pattern names show in the legend on hover and in
// the bar card on click
const pattern: OverlayTemplate = {
  name: "d_pat", totalStep: 2, lock: true, needDefaultPointFigure: false,
  createPointFigures: ({ coordinates, overlay }) => {
    const e = (overlay.extendData ?? {}) as { bull?: boolean };
    const { x, y } = coordinates[0];
    return [{ type: "polygon", ignoreEvent: true, attrs: { coordinates: [{ x, y: y + 5 }, { x: x - 3.5, y: y + 11 }, { x: x + 3.5, y: y + 11 }] },
      styles: { style: "fill", color: e.bull ? "#12b886" : "#868e96" } }];
  },
};

// Drishti pattern marker (server-side, causal; point = the bar's low for bull, high for bear /
// caution; extendData = {text, kind}). Pattern - not a prediction; 'caution' (a bear pattern in an
// uptrend) is a faint note only, not a trade marker.
const signal: OverlayTemplate = {
  name: "d_signal", totalStep: 2, lock: true, needDefaultPointFigure: false,
  createPointFigures: ({ coordinates, overlay }) => {
    const e = (overlay.extendData ?? {}) as { text?: string; kind?: "bull" | "bear" | "caution" };
    const { x, y } = coordinates[0];
    if (e.kind === "caution") return [{ type: "text", ignoreEvent: true,
      attrs: { x, y: y - 6, text: `caution · ${e.text ?? ""}`, align: "center", baseline: "bottom" },
      styles: { color: "rgba(138,148,166,.75)", backgroundColor: "transparent", size: 9, paddingLeft: 0, paddingRight: 0, paddingTop: 0, paddingBottom: 0 } }];
    const bull = e.kind === "bull";
    return [{ type: "text", ignoreEvent: true,
      attrs: { x, y: y + (bull ? 6 : -6), text: `${bull ? "▲" : "▼"} ${e.text ?? ""}`, align: "center", baseline: bull ? "top" : "bottom" },
      styles: { color: "#ffffff", backgroundColor: bull ? "rgba(16,185,129,.9)" : "rgba(244,63,94,.85)", borderRadius: 3,
        paddingLeft: 4, paddingRight: 4, paddingTop: 1, paddingBottom: 1, size: 9 } }];
  },
};

// the stop of a bull pattern marker: a short dashed line at the signal bar's low (extendData = label)
const stop: OverlayTemplate = {
  name: "d_stop", totalStep: 3, lock: true, needDefaultPointFigure: false,
  createPointFigures: ({ coordinates, overlay }) => {
    if (coordinates.length < 2) return [];
    const [a, b] = coordinates as Coord[];
    return [
      { type: "line", ignoreEvent: true, attrs: { coordinates: [a, { x: b.x, y: a.y }] }, styles: { style: "dashed", size: 1, color: "#e03131", dashedValue: [4, 3] } },
      { type: "text", ignoreEvent: true, attrs: { x: b.x + 3, y: a.y, text: String(overlay.extendData ?? "stop"), align: "left", baseline: "middle" },
        styles: { color: "#e03131", backgroundColor: "transparent", size: 9, paddingLeft: 0, paddingRight: 0, paddingTop: 0, paddingBottom: 0 } },
    ];
  },
};

// scanner alert level: a full-width line with its label at the right edge
// (extendData = {label, color, dashed})
const level: OverlayTemplate = {
  name: "d_level", totalStep: 2, lock: true, needDefaultPointFigure: false,
  createPointFigures: ({ coordinates, bounding, overlay }) => {
    const e = (overlay.extendData ?? {}) as { label?: string; color?: string; dashed?: boolean };
    const y = coordinates[0].y, color = e.color ?? "#3a86ff";
    return [
      { type: "line", ignoreEvent: true, attrs: { coordinates: [{ x: 0, y }, { x: bounding.width, y }] },
        styles: { style: e.dashed ? "dashed" : "solid", size: 1.5, color, dashedValue: [6, 4] } },
      { type: "text", ignoreEvent: true, attrs: { x: bounding.width - 6, y: y - 2, text: e.label ?? "", align: "right", baseline: "bottom" },
        styles: { color: "#fff", backgroundColor: color, size: 11, paddingLeft: 4, paddingRight: 4, paddingTop: 1, paddingBottom: 1, borderRadius: 2 } },
    ];
  },
};

// scanner alert bar marker: a ▼ above the bar's high (point = the bar's high)
const mark: OverlayTemplate = {
  name: "d_mark", totalStep: 2, lock: true, needDefaultPointFigure: false,
  createPointFigures: ({ coordinates, overlay }) => {
    const { x, y } = coordinates[0];
    return [
      { type: "polygon", ignoreEvent: true, attrs: { coordinates: [{ x, y: y - 6 }, { x: x - 6, y: y - 16 }, { x: x + 6, y: y - 16 }] }, styles: { style: "fill", color: "#f59f00" } },
      { type: "text", ignoreEvent: true, attrs: { x, y: y - 18, text: String(overlay.extendData ?? "alert"), align: "center", baseline: "bottom" },
        styles: { color: "#f59f00", backgroundColor: "transparent", size: 11, paddingLeft: 0, paddingRight: 0, paddingTop: 0, paddingBottom: 0 } },
    ];
  },
};

let registered = false;
export function registerOverlays() {
  if (registered) return;
  registered = true;
  [rect, measure, note, zones, pattern, signal, stop, level, mark].forEach((o) => registerOverlay(o));
}

// --- user drawings: saved per stock and timeframe in this browser (no DB writes) -------------
export const USER = "user";
export type Saved = { name: string; points: { timestamp?: number; dataIndex?: number; value?: number }[]; extendData?: unknown; lock?: boolean };
const storeKey = (key: string, tf: string) => `drishti.draw.v1.${key}.${tf}`;

export function loadDrawings(key: string, tf: string): Saved[] {
  try { return JSON.parse(localStorage.getItem(storeKey(key, tf)) || "[]"); } catch { return []; }
}
export function saveDrawings(key: string, tf: string, d: Saved[]) {
  try { localStorage.setItem(storeKey(key, tf), JSON.stringify(d)); } catch { /* storage blocked: drawings stay for this page only */ }
}

/** Undo/redo over snapshots of the user drawings. */
export class History {
  private past: Saved[][] = [];
  private future: Saved[][] = [];
  constructor(private cur: Saved[]) {}
  push(next: Saved[]) {
    if (JSON.stringify(next) === JSON.stringify(this.cur)) return;
    this.past.push(this.cur); this.cur = next; this.future = [];
    if (this.past.length > 100) this.past.shift();
  }
  undo(): Saved[] | null { const p = this.past.pop(); if (!p) return null; this.future.push(this.cur); this.cur = p; return p; }
  redo(): Saved[] | null { const f = this.future.pop(); if (!f) return null; this.past.push(this.cur); this.cur = f; return f; }
  get canUndo() { return this.past.length > 0; }
  get canRedo() { return this.future.length > 0; }
}
