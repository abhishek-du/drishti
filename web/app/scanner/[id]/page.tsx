"use client";
// One scanner alert: chart with entry / stop / R1 / R2 and the alert bar, levels, context, the
// journal form (the only write: POST /api/journal/{id}, Track A's API), grades and history.
// Decision support only; PAPER ONLY - there is no order button: the journal records what the
// user did by hand.
import { useParams } from "next/navigation";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useState } from "react";
import type { Tf } from "@/components/kchart/KChart";
import { fmt, get, post, stockHref } from "@/components/api";
import { AlertResp, DecisionLabel, istTime, OutcomePill, rrClass, StatusPill, tgt } from "@/components/scan";

const KChart = dynamic(() => import("@/components/kchart/KChart"), { ssr: false, loading: () => <p className="muted">Loading chart…</p> });
type Status = "watching" | "took" | "skipped";
const BASES: [string, string][] = [["alert", "Alert (rule entry)"], ["took", "Your trade"], ["placebo", "Random entry"]];

export default function AlertPage() {
  const { id } = useParams<{ id: string }>();
  const [r, setR] = useState<AlertResp | null>(null);
  const [err, setErr] = useState("");
  const [tf, setTf] = useState<Tf | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [entry, setEntry] = useState("");
  const [qty, setQty] = useState("");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(() => get<AlertResp>(`/api/scan/alert/${id}`).then((x) => {
    setR(x); setErr("");
    const a = x.alert;
    setTf((t) => t ?? a.tf);
    setStatus(a.status); setEntry(a.actual_entry?.toString() ?? ""); setQty(a.actual_qty?.toString() ?? ""); setNotes(a.notes ?? "");
  }).catch((e) => setErr(String(e))), [id]);
  useEffect(() => { load(); }, [load]);

  const save = async () => {
    if (!r) return;
    const a = r.alert;
    const body: Record<string, unknown> = {};
    if (status && status !== a.status) body.status = status;
    if (entry !== (a.actual_entry?.toString() ?? "")) body.actual_entry = entry === "" ? null : Number(entry);
    if (qty !== (a.actual_qty?.toString() ?? "")) body.actual_qty = qty === "" ? null : Math.round(Number(qty));
    if (notes !== (a.notes ?? "")) body.notes = notes;
    if (status === "took") { body.status = "took"; if (entry !== "") body.actual_entry = Number(entry); }
    setSaving(true); setMsg(null);
    try {
      await post(`/api/journal/${a.id}`, body);
      setMsg({ ok: true, text: "Saved." });
      await load();                    // no optimistic UI: show what the API now holds
    } catch (e) {
      setMsg({ ok: false, text: String(e).replace(/^Error: /, "") });
    } finally { setSaving(false); }
  };

  if (err) return <p className="muted">{err}</p>;
  if (!r || !tf) return <p className="muted">Loading…</p>;
  const a = r.alert, c = a.context ?? {};
  const levels = { entry: a.entry_ref, stop: a.stop, r1: a.r1, r2: a.r2, barTime: a.bar_time, tf: a.tf };
  return (
    <div className="scanpage">
      <DecisionLabel label={r.label} page={`${a.symbol ?? "Alert"} · ${a.detector}`} />
      <div className="row" style={{ gap: 10, margin: "10px 0" }}>
        <a href="/scanner" className="nav-link" style={{ padding: "4px 8px", fontSize: 13 }}>← Scanner</a>
        <h2 style={{ margin: 0 }}><a href={`${stockHref(a.instrument_key)}?tf=${a.tf}`}>{a.symbol ?? a.instrument_key}</a></h2>
        <span className="pill">{a.detector}</span><span className="pill">{a.tf}</span>
        <span className="muted">bar {istTime(a.bar_time, a.tf)} IST · session {a.session_date}</span>
        <StatusPill status={a.status} /><OutcomePill outcome={a.outcome} r={a.r_net} />
      </div>
      <div className="alertgrid">
        <div style={{ minWidth: 0 }}>
          <KChart instrumentKey={a.instrument_key} tf={tf} setTf={setTf} onData={() => {}} levels={levels} />
          <p className="muted small">Lines: entry (blue), stop (red), R1 / R2 (green dashed); ▼ marks the alert bar{tf !== a.tf ? ` (shown on the ${a.tf} chart)` : ""}.</p>
        </div>
        <div className="alertside">
          <div className="card">
            <b>Levels</b>
            <table className="kv"><tbody>
              <tr><td>Entry (ref)</td><td>{fmt(a.entry_ref)}</td></tr>
              <tr><td>Stop</td><td>{fmt(a.stop)}</td></tr>
              <tr><td>Risk / share</td><td>{fmt(a.risk_per_share)}</td></tr>
              <tr><td>Qty</td><td>{a.qty ?? "–"}</td></tr>
              <tr><td>₹ risk</td><td>{fmt(a.risk_rupees, 0)}</td></tr>
              <tr><td>R1 · RR</td><td>{tgt(a.r1)}{a.rr_r1 !== null && <> · <span className={rrClass(a.rr_r1)}>{fmt(a.rr_r1)}</span></>}</td></tr>
              <tr><td>R2 · RR</td><td>{tgt(a.r2)}{a.r2_rr !== null && a.r2_rr !== undefined && <> · <span className={rrClass(a.r2_rr)}>{fmt(a.r2_rr)}</span></>}</td></tr>
              <tr><td>Cost</td><td>{fmt(a.cost_r)} R</td></tr>
              <tr><td>ATR</td><td>{fmt(a.atr)}</td></tr>
              <tr><td>Capital · risk</td><td>{fmt(a.capital, 0)} · {fmt(a.risk_pct)}%</td></tr>
            </tbody></table>
          </div>
          <div className="card">
            <b>Context</b>
            <table className="kv"><tbody>
              <tr><td>Regime</td><td>{c.regime ?? "–"}</td></tr>
              <tr><td>Spec gate</td><td>{c.spec_gate ?? "–"}</td></tr>
              <tr><td>Trend</td><td>{c.trend ?? "–"}</td></tr>
              <tr><td>RSI</td><td>{fmt(c.rsi, 1)}</td></tr>
              <tr><td>Volume ratio (20d)</td><td>{fmt(c.volr20)}</td></tr>
              <tr><td>Candle flags</td><td>{c.flags?.length ? c.flags.join(", ") : "–"}</td></tr>
            </tbody></table>
          </div>
          <div className="card journalform">
            <b>Journal</b>
            <div className="muted small">Records what you did by hand · paper only, no orders.</div>
            <div className="seg" role="radiogroup" aria-label="Status" style={{ margin: "8px 0", display: "flex" }}>
              {(["watching", "took", "skipped"] as Status[]).map((s) => (
                <button key={s} role="radio" aria-checked={status === s} className={status === s ? "sel" : ""} onClick={() => setStatus(s)}>{s[0].toUpperCase() + s.slice(1)}</button>
              ))}
            </div>
            <div className="row" style={{ gap: 8 }}>
              <label className="fld">Actual entry{status === "took" ? " *" : ""}<input type="number" step="0.05" inputMode="decimal" value={entry} onChange={(e) => setEntry(e.target.value)} /></label>
              <label className="fld">Qty{status === "took" ? " *" : ""}<input type="number" step="1" inputMode="numeric" value={qty} onChange={(e) => setQty(e.target.value)} /></label>
            </div>
            <label className="fld">Notes<textarea rows={3} maxLength={4000} value={notes} onChange={(e) => setNotes(e.target.value)} /></label>
            <div className="row" style={{ gap: 8, marginTop: 6 }}>
              <button className="btn-primary" onClick={save} disabled={saving}>{saving ? "Saving…" : "Save"}</button>
              {msg && <span className={msg.ok ? "ok-text" : "err-text"} role={msg.ok ? "status" : "alert"}>{msg.text}</span>}
            </div>
          </div>
          {Object.keys(r.grades).length > 0 && (
            <div className="card">
              <b>Grades</b>
              <div className="scroll"><table className="kv grades"><thead><tr><th></th>{BASES.map(([k, l]) => <th key={k}>{l}</th>)}</tr></thead><tbody>
                <tr><td>Outcome</td>{BASES.map(([k]) => <td key={k}>{r.grades[k] ? <OutcomePill outcome={r.grades[k].outcome} r={null} /> : "–"}</td>)}</tr>
                <tr><td>Entry used</td>{BASES.map(([k]) => <td key={k}>{fmt(r.grades[k]?.entry_used)}</td>)}</tr>
                <tr><td>Exit</td>{BASES.map(([k]) => <td key={k}>{fmt(r.grades[k]?.exit_price)}</td>)}</tr>
                <tr><td>R net</td>{BASES.map(([k]) => <td key={k}>{r.grades[k]?.r_net === null || r.grades[k]?.r_net === undefined ? "–" : `${fmt(r.grades[k].r_net)} R`}</td>)}</tr>
              </tbody></table></div>
            </div>
          )}
          <div className="card">
            <b>History</b>
            {r.journal_history.length === 0 ? <p className="muted small">No journal changes yet.</p> : (
              <ol className="timeline">{r.journal_history.map((h, i) => (
                <li key={i}><span className="muted small">{new Date(h.at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })}</span>{" "}
                  {Object.entries(h.change).map(([k, v]) => `${k}: ${v ?? "–"}`).join(" · ")}</li>
              ))}</ol>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
