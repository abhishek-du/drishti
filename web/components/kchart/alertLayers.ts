// The scanner alert layer of the chart: the actionable entry / stop / R1 / R2 lines, the single
// ALERT marker and the point-in-time S/R bands. Always on: it never reads the pattern layers or
// any layer toggle, so turning the context layers (candlestick patterns, confirmed-pattern tags,
// today's zones) off can never hide it. Pure (no React, no chart instance) so it is unit tested.
import type { OverlayCreate } from "klinecharts";

/** Bar time -> chart timestamp (ms): 1d bars are IST dates, intraday bars epoch seconds. */
export const ts = (t: string | number) => (typeof t === "number" ? t * 1000 : Date.parse(`${t}T00:00:00+05:30`));

/** A zone as the alert saw it (scan_alert.context.zones_used). */
export type SavedZone = { name: string; bottom: number; top: number; touches?: number; first?: string; last?: string; confirmed?: string };
export type AlertLevels = { entry: number; stop: number; r1: number | null; r2: number | null; barTime: string; tf: "1d" | "15m"; zones: SavedZone[] };
/** /api/chart2/bars?as_of=: the zones known on the alert's bar (levels.zones on bars <= it). */
export type ZonesAsOf = { bar: string | number; zones: { price: number; bottom: number; top: number; touches: number; first: string | number; last: string | number }[] } | null;
export type Bar = { timestamp: number; high: number };

export const ALERT_GROUP = "alert";
const C = { entry: "#3a86ff", stop: "#e03131", target: "#2f9e44", saved: "rgba(47,158,68,", asof: "rgba(90,120,170," };
const day = (s: string | number) => String(s).slice(0, 10);

/** The S/R bands of the alert panel: the alert's saved zones when it has them, else the zones
 * known on the alert's bar. Never today's zones (those sit behind their own labelled toggle). */
export function alertSR(levels: AlertLevels, asOf: ZonesAsOf): { source: "saved" | "as_of" | "none"; bands: { top: number; bottom: number; label: string; color: string; xf: number }[] } {
  // xf: where the band's tag sits across the pane; staggered so tags of close bands do not overlap
  const xf = (i: number) => [0.3, 0.45, 0.6][i % 3];
  if (levels.zones.length) return { source: "saved", bands: levels.zones.map((z, i) => ({ top: z.top, bottom: z.bottom, color: C.saved, xf: xf(i),
    label: `${z.name.toUpperCase()} zone (alert's) ${z.bottom.toFixed(2)}–${z.top.toFixed(2)}${z.first ? ` · pivots ${day(z.first)} → ${day(z.last ?? "")}` : ""}${z.confirmed ? ` · confirmed ${day(z.confirmed)}` : ""}` })) };
  if (asOf && asOf.zones.length) return { source: "as_of", bands: asOf.zones.map((z, i) => ({ top: z.top, bottom: z.bottom, color: C.asof, xf: xf(i),
    label: `S/R at the alert bar ×${z.touches} · ${z.price.toFixed(2)}` })) };
  return { source: "none", bands: [] };
}

/** Every overlay of the alert layer, in draw order. `bars` = the chart's bars (oldest first),
 * `chartTf` = the timeframe on screen (the ALERT marker only sits on the alert's own tf). */
export function alertOverlays(levels: AlertLevels, bars: Bar[], chartTf: string, asOf: ZonesAsOf): OverlayCreate[] {
  if (!bars.length) return [];
  const t1 = bars[bars.length - 1].timestamp;
  // R1 / R2 tags sit left of the entry / stop tags, so close levels never hide each other
  const line = (value: number, label: string, color: string, dashed: boolean, offset: number) => ({ name: "d_level", groupId: ALERT_GROUP, lock: true,
    extendData: { label: `${label} ${value.toFixed(2)}`, color, dashed, offset }, points: [{ timestamp: t1, value }] }) as OverlayCreate;
  const ov: OverlayCreate[] = alertSR(levels, asOf).bands.map((b) => ({ name: "d_band", groupId: ALERT_GROUP, lock: true,
    extendData: b, points: [{ timestamp: t1, value: b.top }] }) as OverlayCreate);
  ov.push(line(levels.entry, "Entry", C.entry, false, 0), line(levels.stop, "Stop", C.stop, false, 0));
  if (levels.r1 !== null) ov.push(line(levels.r1, "R1", C.target, true, 96));
  if (levels.r2 !== null) ov.push(line(levels.r2, "R2", C.target, true, 96));
  const at = levels.tf === "1d" ? ts(new Date(levels.barTime).toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" })) : Date.parse(levels.barTime);
  const bar = bars.find((k) => k.timestamp === at);
  if (bar && levels.tf === chartTf) ov.push({ name: "d_mark", groupId: ALERT_GROUP, lock: true, extendData: "ALERT", points: [{ timestamp: at, value: bar.high }] } as OverlayCreate);
  return ov;
}
