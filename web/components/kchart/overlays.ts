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

// Drishti candlestick-pattern glyph (context only, not a signal): small and muted so the alert
// levels stay dominant. extendData = {kind}: bull ▲ under the low, bear ▼ over the high, neutral ·
const pattern: OverlayTemplate = {
  name: "d_pat", totalStep: 2, lock: true, needDefaultPointFigure: false,
  createPointFigures: ({ coordinates, overlay }) => {
    const k = ((overlay.extendData ?? {}) as { kind?: string }).kind ?? "neutral";
    const { x, y } = coordinates[0];
    if (k === "neutral") return [{ type: "circle", ignoreEvent: true, attrs: { x, y: y + 7, r: 1.8 }, styles: { style: "fill", color: "rgba(134,142,150,.6)" } }];
    const tri = k === "bull" ? [{ x, y: y + 5 }, { x: x - 3, y: y + 10 }, { x: x + 3, y: y + 10 }] : [{ x, y: y - 5 }, { x: x - 3, y: y - 10 }, { x: x + 3, y: y - 10 }];
    return [{ type: "polygon", ignoreEvent: true, attrs: { coordinates: tri },
      styles: { style: "fill", color: k === "bull" ? "rgba(18,184,134,.55)" : "rgba(224,49,49,.5)" } }];
  },
};

// confirmed-pattern tag (server-side, causal; point = the bar's low for bull, high for bear /
// caution; extendData = {text, kind}): muted text, no arrow glyph and no buy/sell wording;
// context only. 'caution' (a bear pattern in an uptrend) is fainter still.
const signal: OverlayTemplate = {
  name: "d_signal", totalStep: 2, lock: true, needDefaultPointFigure: false,
  createPointFigures: ({ coordinates, overlay }) => {
    const e = (overlay.extendData ?? {}) as { text?: string; kind?: "bull" | "bear" | "caution" };
    const { x, y } = coordinates[0];
    const bull = e.kind === "bull";
    const color = e.kind === "caution" ? "rgba(138,148,166,.7)" : bull ? "rgba(18,150,110,.9)" : "rgba(200,60,60,.85)";
    return [{ type: "text", ignoreEvent: true,
      attrs: { x, y: y + (bull ? 13 : -13), text: `${e.kind === "caution" ? "caution · " : "✓ "}${e.text ?? ""}`, align: "center", baseline: bull ? "top" : "bottom" },
      styles: { color, backgroundColor: "transparent", size: 9, paddingLeft: 0, paddingRight: 0, paddingTop: 0, paddingBottom: 0 } }];
  },
};

// a confirmed bull pattern tag's reference level: a short faint dashed line at the pattern bar's low
const stop: OverlayTemplate = {
  name: "d_stop", totalStep: 3, lock: true, needDefaultPointFigure: false,
  createPointFigures: ({ coordinates, overlay }) => {
    if (coordinates.length < 2) return [];
    const [a, b] = coordinates as Coord[];
    return [
      { type: "line", ignoreEvent: true, attrs: { coordinates: [a, { x: b.x, y: a.y }] }, styles: { style: "dashed", size: 1, color: "rgba(224,49,49,.45)", dashedValue: [3, 3] } },
      { type: "text", ignoreEvent: true, attrs: { x: b.x + 3, y: a.y, text: String(overlay.extendData ?? "stop"), align: "left", baseline: "middle" },
        styles: { color: "rgba(224,49,49,.6)", backgroundColor: "transparent", size: 9, paddingLeft: 0, paddingRight: 0, paddingTop: 0, paddingBottom: 0 } },
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
        styles: { style: e.dashed ? "dashed" : "solid", size: 2.5, color, dashedValue: [8, 4] } },
      { type: "text", ignoreEvent: true, attrs: { x: bounding.width - 6, y: y - 2, text: e.label ?? "", align: "right", baseline: "bottom" },
        styles: { color: "#fff", backgroundColor: color, size: 12, weight: "bold", paddingLeft: 5, paddingRight: 5, paddingTop: 2, paddingBottom: 2, borderRadius: 3 } },
    ];
  },
};

// scanner alert's saved R1 / R2 zone band (context.zones_used): full width, green, label in the
// middle (clear of the legend at the left and the level tags at the right) (extendData = {top, bottom, label})
const band: OverlayTemplate = {
  name: "d_band", totalStep: 2, lock: true, needDefaultPointFigure: false,
  createPointFigures: ({ bounding, overlay, yAxis }) => {
    if (!yAxis) return [];
    const e = (overlay.extendData ?? {}) as { top: number; bottom: number; label: string };
    const a = yAxis.convertToPixel(e.top), b = yAxis.convertToPixel(e.bottom);
    const y0 = Math.min(a, b), y1 = Math.max(a, b);
    return [
      { type: "rect", ignoreEvent: true, attrs: { x: 0, y: y0, width: bounding.width, height: Math.max(1, y1 - y0) },
        styles: { style: "stroke_fill", color: "rgba(47,158,68,.16)", borderColor: "rgba(47,158,68,.55)", borderSize: 1, borderStyle: "dashed" } },
      { type: "text", ignoreEvent: true, attrs: { x: bounding.width * 0.45, y: y0 - 1, text: e.label, align: "center", baseline: "bottom" },
        styles: { color: "#fff", backgroundColor: "rgba(47,158,68,.85)", size: 10, paddingLeft: 3, paddingRight: 3, paddingTop: 1, paddingBottom: 1, borderRadius: 2 } },
    ];
  },
};

// scanner alert bar marker: a ▼ above the bar's high (point = the bar's high)
const mark: OverlayTemplate = {
  name: "d_mark", totalStep: 2, lock: true, needDefaultPointFigure: false,
  createPointFigures: ({ coordinates, overlay }) => {
    const { x, y } = coordinates[0];
    return [
      { type: "polygon", ignoreEvent: true, attrs: { coordinates: [{ x, y: y - 6 }, { x: x - 9, y: y - 21 }, { x: x + 9, y: y - 21 }] }, styles: { style: "stroke_fill", color: "#f59f00", borderColor: "#fff", borderSize: 1 } },
      { type: "text", ignoreEvent: true, attrs: { x, y: y - 23, text: String(overlay.extendData ?? "ALERT"), align: "center", baseline: "bottom" },
        styles: { color: "#fff", backgroundColor: "#f59f00", size: 11, weight: "bold", paddingLeft: 4, paddingRight: 4, paddingTop: 1, paddingBottom: 1, borderRadius: 3 } },
    ];
  },
};

let registered = false;
export function registerOverlays() {
  if (registered) return;
  registered = true;
  [rect, measure, note, zones, pattern, signal, stop, level, band, mark].forEach((o) => registerOverlay(o));
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
