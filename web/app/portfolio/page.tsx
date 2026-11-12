"use client";
// Paper portfolio pf-v1 (Phase 1 page + Phase 4 curve and stats): READ ONLY (Track A minimal page; Track C owns the full UI and
// the nav link). Portfolios, open positions, recent orders / trades / decisions, the equity
// rows and the frozen pre-registration values. PAPER ONLY: no buttons, no orders.
import { useEffect, useState } from "react";
import { fmt, get, stockHref } from "@/components/api";
import { DecisionLabel } from "@/components/scan";
import EquityCurve from "@/components/EquityCurve";

type Pf = { id: string; kind: string; label: string; research_only: boolean; starting_capital: number;
  cash: number; holdings_value: number; equity: number; peak_equity: number; total_return: number | null;
  drawdown: number | null; open_positions: number; as_of: string; start_date: string };
type Resp = { label: string; note: string; prereg: Record<string, unknown>; portfolios: Pf[] };
type Row = Record<string, string | number | boolean | null>;
type Stats = { trades: Record<string, unknown>; equity: Record<string, number | null> };

const pct = (x: number | null | undefined) => (x === null || x === undefined ? "–" : `${fmt(100 * x, 2)}%`);
const ist = (iso: unknown) => (typeof iso === "string" ? new Date(iso).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" }) : "–");

function Table({ rows, cols }: { rows: Row[]; cols: [string, string][] }) {
  if (!rows.length) return <p className="muted">None yet.</p>;
  return (
    <div className="scroll"><table className="scantable">
      <thead><tr>{cols.map(([k, h]) => <th key={k}>{h}</th>)}</tr></thead>
      <tbody>{rows.map((r, i) => <tr key={i}>{cols.map(([k]) => {
        const v = r[k];
        if (k === "instrument_key" && typeof v === "string") return <td key={k}><a href={stockHref(v)}>{(r.symbol as string) ?? v}</a></td>;
        if (k.endsWith("ts") || k === "as_of") return <td key={k}>{ist(v)}</td>;
        return <td key={k} className={typeof v === "number" ? "num" : ""}>{typeof v === "number" ? fmt(v) : String(v ?? "–")}</td>;
      })}</tr>)}</tbody>
    </table></div>);
}

export default function PortfolioPage() {
  const [d, setD] = useState<Resp | null>(null);
  const [pid, setPid] = useState("main");
  const [pos, setPos] = useState<Row[]>([]);
  const [ord, setOrd] = useState<Row[]>([]);
  const [trd, setTrd] = useState<Row[]>([]);
  const [dec, setDec] = useState<Row[]>([]);
  const [eq, setEq] = useState<Row[]>([]);
  const [st, setSt] = useState<Stats | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { get<Resp>("/api/portfolio").then(setD).catch((e) => setErr(String(e))); }, []);
  useEffect(() => {
    Promise.all([
      get<{ positions: Row[] }>(`/api/portfolio/${pid}/positions`).then((r) => setPos(r.positions)),
      get<{ orders: Row[] }>(`/api/portfolio/${pid}/orders?limit=50`).then((r) => setOrd(r.orders)),
      get<{ trades: Row[] }>(`/api/portfolio/${pid}/trades?limit=50`).then((r) => setTrd(r.trades)),
      get<{ decisions: Row[] }>(`/api/portfolio/${pid}/decisions?limit=50`).then((r) => setDec(r.decisions)),
      get<{ equity: Row[] }>(`/api/portfolio/${pid}/equity`).then((r) => setEq(r.equity)),
      get<Stats>(`/api/portfolio/${pid}/stats`).then(setSt),
    ]).catch((e) => setErr(String(e)));
  }, [pid]);

  return (
    <div className="scanpage">
      <DecisionLabel label={d?.label} page="Paper portfolio" />
      <h2>Paper portfolio (pf-v1)</h2>
      {err && <p className="err">{err}</p>}
      {d && <p className="muted small">{d.note} Frozen rules: {String(d.prereg.doc)} ({String(d.prereg.frozen)}).</p>}
      <div className="scroll"><table className="scantable">
        <thead><tr><th>Portfolio</th><th className="num">Equity</th><th className="num">Cash</th><th className="num">Holdings</th>
          <th className="num">Return</th><th className="num">Drawdown</th><th className="num">Open</th><th>As of</th></tr></thead>
        <tbody>{d?.portfolios.map((p) => (
          <tr key={p.id} className={p.id === pid ? "you" : ""} onClick={() => setPid(p.id)} style={{ cursor: "pointer" }}>
            <td>{p.label}{p.research_only ? " (research only)" : ""}</td><td className="num">{fmt(p.equity)}</td>
            <td className="num">{fmt(p.cash)}</td><td className="num">{fmt(p.holdings_value)}</td>
            <td className="num">{pct(p.total_return)}</td><td className="num">{pct(p.drawdown)}</td>
            <td className="num">{p.open_positions}</td><td>{ist(p.as_of)}</td></tr>))}</tbody>
      </table></div>
      <h3>Open positions · {pid}</h3>
      <Table rows={pos} cols={[["instrument_key", "Symbol"], ["book", "Book"], ["product", "Product"], ["qty", "Qty"], ["avg_price", "Avg"],
        ["last_price", "Last"], ["unrealized_pnl", "Unrealised (net)"], ["stop", "Stop"], ["target", "Target"], ["opened_ts", "Opened"]]} />
      <h3>Equity vs Nifty 500 · {pid}</h3>
      <EquityCurve rows={eq as never} start={d?.portfolios.find((p) => p.id === pid)?.starting_capital ?? 1000000} />
      {st && Number(st.trades.trades) > 0 && (
        <p className="small">Trades {String(st.trades.trades)} · hit rate {pct(st.trades.hit_rate as number)} · payoff {String(st.trades.payoff_ratio ?? "–")} ·
          expectancy ₹{fmt(st.trades.expectancy_rs as number)} ({String(st.trades.expectancy_r ?? "–")} R) · costs ₹{fmt(st.trades.costs as number)} ·
          max drawdown {pct(st.equity.max_drawdown)} · vs Nifty 500 {pct(st.equity.excess_return)} (net of costs; display only, the one look is pre-registered)</p>)}
      <h3>Equity (daily, net of costs)</h3>
      <Table rows={eq} cols={[["date", "Date"], ["equity", "Equity"], ["cash", "Cash"], ["daily_pnl", "Day P&L"], ["drawdown", "Drawdown"],
        ["benchmark_equity", "Benchmark"], ["placebo_equity", "Placebo"]]} />
      <h3>Trades</h3>
      <Table rows={trd} cols={[["exit_ts", "Exit"], ["instrument_key", "Symbol"], ["book", "Book"], ["qty", "Qty"], ["entry_price", "Entry"],
        ["exit_price", "Exit px"], ["costs", "Costs"], ["net_pnl", "Net P&L"], ["r_multiple", "R"], ["reason_out", "Why out"]]} />
      <h3>Orders</h3>
      <Table rows={ord} cols={[["ts", "Time"], ["instrument_key", "Symbol"], ["book", "Book"], ["side", "Side"], ["product", "Product"], ["qty", "Qty"],
        ["fill_price", "Fill"], ["total_costs", "Costs"], ["status", "Status"], ["reason_code", "Reason"], ["reason_text", "Why"]]} />
      <h3>Decisions (taken and skipped)</h3>
      <Table rows={dec} cols={[["ts", "Time"], ["instrument_key", "Symbol"], ["candidate_from", "From"], ["tf", "TF"], ["decision", "Decision"],
        ["reason_code", "Code"], ["why", "Why"]]} />
    </div>);
}
