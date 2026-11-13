"use client";
// Live RULE VERDICT tag per timeframe (user 2026-11-12), above the chart. Not advice and not a
// prediction: what the frozen paper-portfolio rules (pf-v1) say about buying this stock now,
// with the Time Machine summary in points. Refreshes every 10 s in market hours.
import { useEffect, useState } from "react";
import { get } from "@/components/api";

type V = { tag: string; tone: "pass" | "skip" | "info"; reasons: string[]; plan: string | null; time_machine: string[];
  checks?: { gate: string; pass: boolean; why: string }[] };
type R = { verdicts: Record<string, V> & { note: string }; price: { ltp: number | null }; as_of: string; error?: string };

const TONE: Record<V["tone"], string> = { pass: "on", skip: "off", info: "" };

export function marketOpen(): boolean {
  const p = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(new Date());
  const g = (t: string) => p.find((x) => x.type === t)?.value ?? "";
  const m = parseInt(g("hour")) * 60 + parseInt(g("minute"));
  return !["Sat", "Sun"].includes(g("weekday")) && m >= 555 && m <= 940;
}

export default function VerdictBar({ instrumentKey, tf }: { instrumentKey: string; tf: string }) {
  const [r, setR] = useState<R | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    let stop = false;
    const load = () => get<R>(`/api/analysis/now?key=${encodeURIComponent(instrumentKey)}`)
      .then((x) => { if (!stop) setR(x); }).catch(() => { /* keep the last tag */ });
    load();
    const iv = setInterval(() => { if (marketOpen() && !document.hidden) load(); }, 10000);
    return () => { stop = true; clearInterval(iv); };
  }, [instrumentKey]);
  if (!r || r.error || !r.verdicts) return null;
  const v = r.verdicts[tf] ?? r.verdicts["1d"];
  const t = new Date(r.as_of).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return (
    <div className="card" style={{ marginBottom: 8, padding: "10px 12px" }}>
      <div className="row" style={{ gap: 6, flexWrap: "wrap", alignItems: "center", justifyContent: "space-between" }}>
        <div className="row" style={{ gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          <b className="small">Rule verdict · {tf}</b>
          <span className={`pill ${TONE[v.tone]}`} style={{ fontWeight: 700, fontSize: 11.5 }}>{v.tag}</span>
        </div>
        <button className="btn small" onClick={() => setOpen(!open)} style={{ cursor: "pointer", fontSize: 11 }}>
          {open ? "▲ less" : "▼ more"}
        </button>
      </div>
      <div className="row" style={{ gap: 4, flexWrap: "wrap", margin: "6px 0 4px" }}>
        {(["1d", "15m", "5m", "1m"] as const).filter((x) => x !== tf).map((x) => (
          <span key={x} className={`pill ${TONE[r.verdicts[x].tone]}`} style={{ fontSize: 10, opacity: 0.85, padding: "1px 6px" }}>
            {x}: {r.verdicts[x].tag}
          </span>
        ))}
      </div>
      <div className="muted small" style={{ fontSize: 11, margin: "2px 0 0" }}>
        LTP {r.price.ltp ?? "–"} · {t} IST{marketOpen() ? " · live" : ""}
      </div>
      {open && (
        <div className="small" style={{ marginTop: 8, borderTop: "1px solid var(--line)", paddingTop: 6 }}>
          <ul style={{ margin: "2px 0 4px 16px", padding: 0 }}>{v.reasons.map((x) => <li key={x} style={{ marginBottom: 2 }}>{x}</li>)}</ul>
          {v.plan && <div className="muted" style={{ margin: "4px 0" }}>Plan: {v.plan}</div>}
          {v.time_machine.length > 0 && (
            <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>{v.time_machine.map((x) => <li key={x} style={{ marginBottom: 2 }}>{x}</li>)}</ul>
          )}
          <div className="muted" style={{ marginTop: 4, fontSize: 10.5 }}>{r.verdicts.note}</div>
        </div>
      )}
    </div>
  );
}
