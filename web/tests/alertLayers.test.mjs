// The scanner alert layer must always render: entry / stop / R1 / R2 lines, the ALERT marker and
// the point-in-time S/R bands, with both pattern layers OFF (they are not inputs at all).
// Run: node --test web/tests  (node >= 23 strips the TypeScript types of alertLayers.ts)
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { alertOverlays, alertSR, ALERT_GROUP, ts } from "../components/kchart/alertLayers.ts";

const bars = ["2026-10-30", "2026-11-02", "2026-11-03"].map((d, i) => ({ timestamp: ts(d), high: 100 + i }));
const levels = { entry: 1936.97, stop: 1843.55, r1: 1960.49, r2: 2017.64, barTime: "2026-11-03T13:50:00+00:00", tf: "1d", zones: [] };
const asOf = { bar: "2026-11-03", zones: [{ price: 1975.9, bottom: 1960.5, top: 1991.4, touches: 2, first: "2026-08-15", last: "2026-09-29" },
                                         { price: 1794.9, bottom: 1779.4, top: 1810.4, touches: 2, first: "2026-06-01", last: "2026-07-01" }] };
const labels = (ov) => ov.filter((o) => o.name === "d_level").map((o) => o.extendData.label.split(" ")[0]);

test("alert page, both pattern layers off: entry / stop / R1 / R2 lines, ALERT marker and S/R bands", () => {
  const layers = { zones: true, patterns: "off", trend: true, signals: false };   // what the page has by default
  assert.equal(alertOverlays.length, 4, "the alert layer takes no layer settings");
  const ov = alertOverlays(levels, bars, "1d", asOf, layers);
  assert.deepEqual(labels(ov), ["Entry", "Stop", "R1", "R2"]);
  assert.equal(ov.filter((o) => o.name === "d_band").length, 2, "S/R bands present");
  assert.equal(ov.filter((o) => o.name === "d_mark").length, 1, "one ALERT marker");
  assert.ok(ov.every((o) => o.groupId === ALERT_GROUP && !["d_pat", "d_signal", "d_stop"].includes(o.name)));
});

test("S/R: the alert's saved zones first, else the zones known at the alert bar, never empty when either exists", () => {
  const saved = { ...levels, zones: [{ name: "r1", bottom: 1960.49, top: 1991.41 }, { name: "r2", bottom: 2017.64, top: 2048.56 }] };
  const s = alertSR(saved, asOf);
  assert.equal(s.source, "saved");
  assert.deepEqual(s.bands.map((b) => [b.bottom, b.top]), [[1960.49, 1991.41], [2017.64, 2048.56]]);
  assert.equal(alertSR(levels, asOf).source, "as_of");
  assert.match(alertSR(levels, asOf).bands[0].label, /S\/R at the alert bar/);
  assert.equal(alertSR(levels, null).source, "none");
});

test("no R2 / no R1: the other lines still render; R1 / R2 tags sit apart from the entry tag", () => {
  const ov = alertOverlays({ ...levels, r1: 429.69, r2: null, entry: 428.21 }, bars, "1d", asOf);
  assert.deepEqual(labels(ov), ["Entry", "Stop", "R1"]);
  const off = (n) => ov.find((o) => o.name === "d_level" && o.extendData.label.startsWith(n)).extendData.offset;
  assert.notEqual(off("Entry"), off("R1"));
  assert.deepEqual(labels(alertOverlays({ ...levels, r1: null, r2: null }, bars, "1d", null)), ["Entry", "Stop"]);
});

test("another timeframe on screen: lines and S/R stay, the ALERT marker only on the alert's own timeframe", () => {
  const ov = alertOverlays(levels, bars, "1h", asOf);
  assert.deepEqual(labels(ov), ["Entry", "Stop", "R1", "R2"]);
  assert.equal(ov.filter((o) => o.name === "d_mark").length, 0);
});

test("KChart: the alert-layer effect reads no layer toggle (patterns, confirmed tags, today's zones)", () => {
  const src = readFileSync(new URL("../components/kchart/KChart.tsx", import.meta.url), "utf8");
  const a = src.indexOf("// scanner alert layer"), b = src.indexOf("// Drishti layers", a);
  assert.ok(a > 0 && b > a);
  const block = src.slice(a, b);
  assert.match(block, /alertOverlays\(/);
  assert.doesNotMatch(block, /layers\.|todayZ|showZones/);
  // and the context layers never remove the alert group
  assert.doesNotMatch(src.slice(b), /removeOverlay\(\{ groupId: ALERT_GROUP \}\)|groupId: "alert"/);
});
