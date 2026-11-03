"use client";
// Journal / stats: your trades vs the same alerts at the rule entry, all alerts and a random-
// entry placebo, in R only (no equity curve, no rupee P&L headline). Decision support only.
import { useEffect, useState } from "react";
import { fmt, get } from "@/components/api";
import { DecisionLabel, Group, Groups, StatsResp } from "@/components/scan";

const ROWS: [keyof Groups, string][] = [
  ["took", "Your trades (took)"], ["took_base", "Same alerts, rule entry (took_base)"],
  ["alerts", "All alerts (base rate)"], ["placebo", "Random entry (placebo)"],
];
const pct = (x: number | null) => (x === null ? "–" : `${fmt(100 * x, 1)}%`);
const r = (x: number | null) => (x === null ? "–" : `${x >= 0 ? "+" : ""}${fmt(x)}`);
const today = () => new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });

function StatsTable({ g }: { g: Groups }) {
  const row = (k: keyof Groups, label: string) => { const x: Group = g[k]; return (
    <tr key={k} className={k === "took" ? "you" : k === "placebo" ? "placebo" : ""}>
      <td>{label}</td><td className="num">{x.closed}</td><td className="num">{pct(x.hit_rate_r1)}</td><td className="num">{pct(x.win_rate)}</td>
      <td className="num">{r(x.expectancy_r_net)}</td><td className="num">{r(x.avg_win_r)}</td><td className="num">{r(x.avg_loss_r)}</td>
      <td className="num">{x.payoff_ratio === null ? "–" : fmt(x.payoff_ratio)}</td><td className="num">{r(x.total_r_net)}</td>
    </tr>); };
  return (
    <div className="scroll"><table className="scantable stats">
      <thead><tr><th></th><th className="num">Closed</th><th className="num">Hit rate to R1</th><th className="num">Win rate</th><th className="num">Expectancy R (net)</th>
        <th className="num">Avg win R</th><th className="num">Avg loss R</th><th className="num">Payoff</th><th className="num">Total R</th></tr></thead>
      <tbody>{ROWS.map(([k, l]) => row(k, l))}</tbody>
    </table></div>
  );
}

export default function Journal() {
  const [since, setSince] = useState("2026-11-01");
  const [until, setUntil] = useState(today());
  const [tf, setTf] = useState<"" | "1d" | "15m">("");
  const [d, setD] = useState<StatsResp | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    const q = new URLSearchParams({ since, until, ...(tf ? { tf } : {}) });
    get<StatsResp>(`/api/journal/stats?${q}`).then((x) => { setD(x); setErr(""); }).catch((e) => setErr(String(e)));
  }, [since, until, tf]);
  const jc = d?.journal_counts ?? {};
  return (
    <div className="scanpage">
      <DecisionLabel label={d?.label} page="Journal" />
      <div className="row" style={{ justifyContent: "space-between", margin: "10px 0" }}>
        <h2 style={{ margin: 0 }}>Journal</h2>
        <div className="row" style={{ gap: 8 }}>
          <label className="fld inline">From<input type="date" value={since} onChange={(e) => setSince(e.target.value)} /></label>
          <label className="fld inline">To<input type="date" value={until} onChange={(e) => setUntil(e.target.value)} /></label>
          <select value={tf} onChange={(e) => setTf(e.target.value as "" | "1d" | "15m")} aria-label="Timeframe">
            <option value="">All timeframes</option><option value="1d">1d</option><option value="15m">15m</option>
          </select>
        </div>
      </div>
      {err && <p className="muted">{err}</p>}
      {d && (<>
        <div className="card scanbox">
          <StatsTable g={d.overall} />
          <p className="small" style={{ margin: "8px 12px 4px" }}><b>Compare Your trades with Random entry, not with zero.</b></p>
          <p className="muted small" style={{ margin: "0 12px 10px" }}>{d.note}</p>
        </div>
        <div className="row counts">
          {["watching", "took", "skipped", "none"].map((k) => <span key={k} className="card mini">{k} <b>{jc[k] ?? 0}</b></span>)}
          <span className="card mini">open alerts <b>{d.alerts_open}</b></span>
          <span className="card mini">no-fill alerts <b>{d.alerts_no_fill}</b></span>
        </div>
        <h3>By detector</h3>
        {Object.entries(d.by_detector).map(([name, g]) => (
          <details key={name} className="card detbox">
            <summary><b>{name}</b> <span className="muted small">· took {g.took.closed} closed · alerts {g.alerts.closed} closed</span></summary>
            <StatsTable g={g} />
          </details>
        ))}
        <p className="muted small">R only; no equity curve and no ₹ P&L headline. {d.version} · {d.since} → {d.until}{d.tf ? ` · ${d.tf}` : ""}.</p>
      </>)}
      {!d && !err && <p className="muted">Loading…</p>}
    </div>
  );
}
