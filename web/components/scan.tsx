"use client";
// Scanner + journal types and small shared pieces. Decision support only: detections are NOT
// validated edges; PAPER ONLY (there is no order button anywhere; the journal records what the
// user did by hand). The label always comes from the API's `label` field.
import { useEffect } from "react";
import { fmt } from "./api";

export type Ctx = { regime?: string; spec_gate?: string; close?: number; rsi?: number; volr20?: number; trend?: string; flags?: string[]; bar_source?: string };
export type Alert = {
  id: number; session_date: string; tf: "1d" | "15m"; bar_time: string; instrument_key: string; symbol: string | null;
  detector: string; entry_ref: number; stop: number; r1: number | null; r2: number | null; rr_r1: number | null;
  qty: number | null; capital: number | null; risk_pct: number | null; cost_r: number | null; atr: number | null; context: Ctx;
  created_at: string; status: "watching" | "took" | "skipped" | null; actual_entry: number | null; actual_qty: number | null;
  notes: string | null; outcome: string | null; r_net: number | null;
  risk_per_share?: number; risk_rupees?: number; r2_rr?: number | null;
};
export type AlertsResp = { label: string; version: string; session: string | null; dates: string[]; detectors: string[]; alerts: Alert[] };
export type Grade = { basis: string; outcome: string; entry_used: number | null; exit_price: number | null; r_net: number | null; bars_used: number | null; graded_at: string };
export type AlertResp = { label: string; alert: Alert; grades: Record<string, Grade>; journal_history: { change: Record<string, unknown>; at: string }[] };
export type Group = { closed: number; hit_rate_r1: number | null; win_rate: number | null; expectancy_r_net: number | null; avg_win_r: number | null;
  avg_loss_r: number | null; payoff_ratio: number | null; total_r_net: number | null; by_outcome: Record<string, number> };
export type Groups = Record<"took" | "took_base" | "alerts" | "placebo", Group>;
export type StatsResp = { label: string; version: string; since: string; until: string; tf: string | null; overall: Groups; by_detector: Record<string, Groups>;
  journal_counts: Record<string, number>; alerts_open: number; alerts_no_fill: number; note: string };

/** The mandatory label, verbatim from the API, at the top of the page and as the tab-title suffix. */
export function DecisionLabel({ label, page }: { label: string | null | undefined; page: string }) {
  useEffect(() => { if (label) document.title = `${page} · Drishti — ${label}`; }, [label, page]);
  if (!label) return null;
  return <div className="dlabel" role="note">{label}</div>;
}

export const istTime = (iso: string, tf: string) => {
  const d = new Date(iso);
  return tf === "1d"
    ? d.toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" })
    : d.toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
};
/** RR colour hint only (never a filter): red < 1.5, amber 1.5-2, green >= 2. */
export const rrClass = (rr: number | null) => (rr === null ? "" : rr < 1.5 ? "rr-low" : rr < 2 ? "rr-mid" : "rr-ok");
export const OUTCOME_CLASS: Record<string, string> = { R1: "on", STOP: "off", TIMEOUT: "grey", OPEN: "outline", NO_FILL: "muted-pill" };

export function OutcomePill({ outcome, r }: { outcome: string | null; r: number | null }) {
  if (!outcome) return <span className="muted">–</span>;
  return <span className={`pill ${OUTCOME_CLASS[outcome] ?? ""}`}>{outcome}{r !== null && r !== undefined ? ` ${r >= 0 ? "+" : ""}${fmt(r)}R` : ""}</span>;
}
export function StatusPill({ status }: { status: string | null }) {
  return status ? <span className={`pill st-${status}`}>{status}</span> : <span className="muted">none</span>;
}
/** Target text: R1/R2 can be null when there is no resistance above (the grader then uses entry + 2R). */
export const tgt = (x: number | null) => (x === null ? "no resistance above → 2R" : fmt(x));
