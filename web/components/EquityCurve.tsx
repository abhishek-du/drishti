"use client";
// pf-v1 Phase 4: the book's equity vs the Nifty 500 benchmark, as % return from the start
// capital (daily snapshots, net of costs). PAPER ONLY. Plain SVG, no chart library.
type Pt = { date: string; equity: number | null; benchmark_equity: number | null };

export default function EquityCurve({ rows, start }: { rows: Pt[]; start: number }) {
  const pts = rows.filter((r) => r.equity !== null);
  if (pts.length < 2) return <p className="muted small">The curve starts after two daily snapshots.</p>;
  const W = 640, H = 200, P = 28;
  const ret = (v: number | null) => (v === null ? null : (100 * v) / start - 100);
  const a = pts.map((r) => ret(r.equity) as number);
  const b = pts.map((r) => ret(r.benchmark_equity));
  const all = [...a, ...b.filter((x): x is number => x !== null), 0];
  const lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || 1;
  const x = (i: number) => P + (i * (W - 2 * P)) / (pts.length - 1);
  const y = (v: number) => H - P - ((v - lo) * (H - 2 * P)) / span;
  const path = (vs: (number | null)[]) => vs.map((v, i) => (v === null ? "" : `${i && vs[i - 1] !== null ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`)).join("");
  const last = (vs: (number | null)[]) => [...vs].reverse().find((v) => v !== null) ?? null;
  const f = (v: number | null) => (v === null ? "–" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`);
  return (
    <div className="card" style={{ padding: 8 }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Equity vs Nifty 500">
        <line x1={P} x2={W - P} y1={y(0)} y2={y(0)} stroke="var(--line-strong)" strokeDasharray="3 3" />
        <path d={path(b)} fill="none" stroke="var(--muted)" strokeWidth={1.5} />
        <path d={path(a)} fill="none" stroke="var(--accent)" strokeWidth={2} />
        <text x={P} y={14} fontSize={11} fill="var(--fg-secondary)">{pts[0].date} → {pts[pts.length - 1].date}</text>
        <text x={W - P} y={y(hi) + 4} fontSize={10} textAnchor="end" fill="var(--muted)">{f(hi)}</text>
        <text x={W - P} y={y(lo) - 2} fontSize={10} textAnchor="end" fill="var(--muted)">{f(lo)}</text>
      </svg>
      <div className="small row" style={{ gap: 16 }}>
        <span style={{ color: "var(--accent)" }}>■ book {f(last(a))}</span>
        <span style={{ color: "var(--muted)" }}>■ Nifty 500 {f(last(b))}</span>
      </div>
    </div>);
}
