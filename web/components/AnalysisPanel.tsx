"use client";
// "Analysis now" for one stock (user 2026-11-12): live price, context, levels, the plan if bought
// now with its Time Machine odds, events and scanner state. DECISION SUPPORT, NOT A PREDICTION.
// Refreshes every 30 s during market hours (Mon-Fri 09:15-15:40 IST), once otherwise.
import { useCallback, useEffect, useState } from "react";
import { fmt, get } from "@/components/api";

type Num = number | null;
type A = {
  label: string; symbol: string | null; as_of: string; error?: string;
  history: { daily_bars_until: string; previous_session: string; lagging: boolean; note: string | null; provisional_filled: string[] };
  price: { ltp: Num; source: string; prev_close: Num; prev_close_source: string; change_pct: Num; today: { open: Num; high: Num; low: Num } };
  context: { regime: string | null; spec_gate: string | null; regime_session: string | null; trend: string; trend_rule: string;
    ma_trend_up: boolean; ma_trend_rule: string; sma50: Num; sma200: Num;
    above_sma50: boolean; above_sma200: boolean; rsi14: Num; atr: Num; atr_pct: Num; hi52: Num; pct_below_52w_high: Num };
  levels: { support: { bottom: Num; top: Num; touches: number } | null; resistance: { bottom: Num; top: Num; touches: number } | null;
    pct_to_support: Num; pct_to_resistance: Num };
  plan_if_bought_now: { entry: Num; stop: Num; risk_per_share: Num; stop_basis: string; r1: Num; r2: Num; target: Num; target_basis: string;
    tags: string[]; rr_gross: Num; net_rr: Num; qty_1pct_of_10L: number; cost_r: Num; horizon_sessions: number };
  odds: { verdict?: string; breakeven?: Num; replay_n?: number; thin_sample?: boolean; paper_gate_pass?: boolean; error?: string;
    mc?: { target_first: Num; stop_first: Num; neither: Num; green: Num; avg_r_net: Num; median_r_net: Num; p05_r_net: Num; p95_r_net: Num };
    pool?: { first: string | null; last: string | null; n_days: number }; caveat?: string } | null;
  events: { results_in_hold: boolean; code: string | null; why: string; window: string[] };
  scanner: { alerts_today: { id: number; tf: string; detector: string; bar_time: string; entry_ref: Num; stop: Num }[];
    radar_today: { bar_time: string; reason: string[] } | null; watchlist_rank: number | null };
};

const pct = (x: Num, d = 1) => (x === null ? "–" : `${x >= 0 ? "+" : ""}${fmt(x, d)}%`);
const p100 = (x: Num) => (x === null ? "–" : `${fmt(100 * x, 0)}%`);
const r = (x: Num) => (x === null ? "–" : `${x >= 0 ? "+" : ""}${fmt(x)} R`);
const ist = (iso: string) => new Date(iso).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", second: "2-digit" });

function marketOpen(): boolean {
  const p = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(new Date());
  const g = (t: string) => p.find((x) => x.type === t)?.value ?? "";
  const m = parseInt(g("hour")) * 60 + parseInt(g("minute"));
  return !["Sat", "Sun"].includes(g("weekday")) && m >= 555 && m <= 940;
}

export default function AnalysisPanel({ instrumentKey }: { instrumentKey: string }) {
  const [a, setA] = useState<A | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => {
    get<A>(`/api/analysis/now?key=${encodeURIComponent(instrumentKey)}`).then((x) => { setA(x); setErr(null); }).catch((e) => setErr(String(e)));
  }, [instrumentKey]);
  useEffect(() => {
    load();
    const iv = setInterval(() => { if (marketOpen() && !document.hidden) load(); }, 10000);
    return () => clearInterval(iv);
  }, [load]);

  if (err) return <div className="card"><p className="err">{err}</p></div>;
  if (!a) return <div className="card"><p className="muted">Loading analysis…</p></div>;
  if (a.error) return <div className="card"><p className="muted">{a.error}</p></div>;
  const { price, context: c, levels: l, plan_if_bought_now: p, odds: o, events: e, scanner: s } = a;
  return (
    <section className="card" style={{ marginTop: 12 }}>
      <div className="dlabel" role="note">{a.label}</div>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <h3 style={{ margin: "6px 0" }}>Analysis now · {a.symbol ?? instrumentKey}</h3>
        <span className="muted small">updated {ist(a.as_of)} IST{marketOpen() ? " · refreshes every 10 s" : ""}</span>
      </div>
      {a.history.provisional_filled.length > 0 && <p className="muted small">Official daily bar not stored yet for {a.history.provisional_filled.join(", ")}: Drishti&apos;s 15:40 provisional bar is used until it arrives.</p>}
      {a.history.lagging && <p className="pill warn small">{a.history.note} (indicators use the stored history + today&apos;s live bar)</p>}
      <div className="grid2" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 12 }}>
        <div>
          <b>Price</b>
          <p style={{ fontSize: 22, margin: "4px 0" }}>{fmt(price.ltp)} <span className={price.change_pct !== null && price.change_pct < 0 ? "neg" : "pos"}>{pct(price.change_pct, 2)}</span></p>
          <p className="muted small">prev close {fmt(price.prev_close)} · today O {fmt(price.today.open)} H {fmt(price.today.high)} L {fmt(price.today.low)} · {price.source}</p>
          <b>Context</b>
          <ul className="small">
            <li>Market regime: <b>{c.regime ?? "–"}</b> (spec gate {c.spec_gate ?? "–"}, as of {c.regime_session})</li>
            <li title={c.ma_trend_rule}>MA trend (Scout&apos;s rule): <b>{c.ma_trend_up ? "UP" : "not UP"}</b> · {c.above_sma50 ? "above" : "below"} SMA50 {fmt(c.sma50)} · {c.above_sma200 ? "above" : "below"} SMA200 {fmt(c.sma200)}</li>
            <li title={c.trend_rule}>Swing structure (pivots): <b>{c.trend}</b>{c.ma_trend_up && c.trend === "SIDE" ? " — long-term uptrend, short-term range" : ""}</li>
            <li>RSI14 {fmt(c.rsi14, 1)} · ATR {fmt(c.atr)} ({fmt(c.atr_pct, 1)}%) · {fmt(c.pct_below_52w_high, 1)}% below the 52-week high {fmt(c.hi52)}</li>
            <li>Support: {l.support ? `${fmt(l.support.bottom)}–${fmt(l.support.top)} (${l.support.touches}×, ${pct(l.pct_to_support)})` : "none found below"}</li>
            <li>Resistance: {l.resistance ? `${fmt(l.resistance.bottom)}–${fmt(l.resistance.top)} (${l.resistance.touches}×, ${pct(l.pct_to_resistance)})` : "none found above"}</li>
          </ul>
        </div>
        <div>
          <b>If bought now (paper plan)</b>
          <ul className="small">
            <li>Entry {fmt(p.entry)} · stop {fmt(p.stop)} ({p.stop_basis}) · risk {fmt(p.risk_per_share)}/share</li>
            <li>Target {fmt(p.target)} ({p.target_basis}){p.r1 !== null ? ` · R1 ${fmt(p.r1)}` : ""}{p.r2 !== null ? ` · R2 ${fmt(p.r2)}` : ""}{p.tags.length ? ` · ${p.tags.join(", ")}` : ""}</li>
            <li>R:R gross {fmt(p.rr_gross)} · <b>net {fmt(p.net_rr)}</b> · costs {fmt(p.cost_r, 3)} R · qty {p.qty_1pct_of_10L} (1% of ₹10 lakh) · {p.horizon_sessions}-session horizon</li>
          </ul>
          <b>Time Machine odds (this stock&apos;s history, bars before today)</b>
          {o && o.mc ? (
            <ul className="small">
              <li>Target first {p100(o.mc.target_first)} · stop first {p100(o.mc.stop_first)} · neither {p100(o.mc.neither)} · net break-even {p100(o.breakeven ?? null)}</li>
              <li>Avg net {r(o.mc.avg_r_net)} · median {r(o.mc.median_r_net)} · 5–95% {r(o.mc.p05_r_net)} … {r(o.mc.p95_r_net)}</li>
              <li>Replay N {o.replay_n}{o.thin_sample ? " (thin sample)" : ""} · paper-portfolio odds gate: <b>{o.paper_gate_pass ? "would pass" : "would fail"}</b></li>
              <li className="muted">{o.verdict}</li>
            </ul>
          ) : <p className="muted small">{o?.error ?? "no odds"}</p>}
          <b>Events &amp; scanner</b>
          <ul className="small">
            <li>Results in the next {p.horizon_sessions} sessions ({e.window.join(" … ")}): {e.results_in_hold ? <b>{e.code === "SKIP_RESULTS_UNKNOWN" ? "UNKNOWN" : "YES"}</b> : "none"} — {e.why}</li>
            <li>Watchlist: {s.watchlist_rank ? `#${s.watchlist_rank}` : "not on it"} · Live Radar today: {s.radar_today ? `${ist(s.radar_today.bar_time)} (${s.radar_today.reason.join(", ")})` : "no"}</li>
            <li>Scanner alerts today: {s.alerts_today.length ? s.alerts_today.map((x) => `${x.detector} [${x.tf}] ${ist(x.bar_time)}`).join(" · ") : "none"}</li>
          </ul>
        </div>
      </div>
      {o?.caveat && <p className="muted small">{o.caveat}</p>}
    </section>
  );
}
