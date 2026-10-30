// Drawing tools (built-in KLineChart overlays plus a few of our own) and the Drishti layers.
import { OverlayTemplate, registerOverlay } from "klinecharts";

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
type Pt = { value?: number; dataIndex?: number; timestamp?: number };

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
    const dl = chart.getDataList();   // restored points carry only timestamps
    const idx = (x: Pt) => { const i = x.timestamp === undefined ? -1 : dl.findIndex((k) => k.timestamp === x.timestamp); return i >= 0 ? i : (x.dataIndex ?? 0); };
    const bars = idx(q) - idx(p);
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

// Drishti S/R zone: a band across the whole width (points: top, bottom; label in extendData)
const zone: OverlayTemplate = {
  name: "d_zone", totalStep: 3, lock: true, needDefaultPointFigure: false,
  createPointFigures: ({ coordinates, bounding, overlay }) => {
    if (coordinates.length < 2) return [];
    const y0 = Math.min(coordinates[0].y, coordinates[1].y), y1 = Math.max(coordinates[0].y, coordinates[1].y);
    return [
      { type: "rect", ignoreEvent: true, attrs: { x: 0, y: y0, width: bounding.width, height: Math.max(1, y1 - y0) }, styles: { style: "fill", color: "rgba(120,120,140,.14)" } },
      { type: "text", ignoreEvent: true, attrs: { x: 4, y: y0, text: String(overlay.extendData ?? ""), align: "left", baseline: "bottom" }, styles: { color: "#667085", backgroundColor: "transparent", size: 11, paddingLeft: 0, paddingRight: 0, paddingTop: 0, paddingBottom: 1 } },
    ];
  },
};

// Drishti candle-pattern label under a bar (point = the bar's low; extendData = {text, bull})
const pattern: OverlayTemplate = {
  name: "d_pat", totalStep: 2, lock: true, needDefaultPointFigure: false,
  createPointFigures: ({ coordinates, overlay }) => {
    const e = (overlay.extendData ?? {}) as { text?: string; bull?: boolean };
    return [{ type: "text", ignoreEvent: true, attrs: { x: coordinates[0].x, y: coordinates[0].y + 6, text: `▲ ${e.text ?? ""}`, align: "center", baseline: "top" },
      styles: { color: e.bull ? "#12805c" : "#868e96", backgroundColor: "transparent", size: 10, paddingLeft: 0, paddingRight: 0, paddingTop: 0, paddingBottom: 0 } }];
  },
};

let registered = false;
export function registerOverlays() {
  if (registered) return;
  registered = true;
  [rect, measure, note, zone, pattern].forEach((o) => registerOverlay(o));
}

// --- user drawings: saved per stock and timeframe in this browser (no DB writes) -------------
export const USER = "user";
export type Saved = { name: string; points: { timestamp?: number; value?: number }[]; extendData?: unknown; lock?: boolean };
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
