"use client";
// Scanner: the day's detector alerts (decision support only; detections are NOT validated edges;
// PAPER ONLY, no order button). Read only: the journal is edited on the alert page.
import { useCallback, useEffect, useMemo, useState } from "react";
import { fmt, get, stockHref } from "@/components/api";
import { AlertsResp, D5_NOTE, DecisionLabel, istTime, OutcomePill, rrClass, StatusPill } from "@/components/scan";

type Sort = { key: "rr" | "detector" | "symbol"; dir: 1 | -1 };
const NEW_MIN = 15;

/** 09:15-15:35 IST on a weekday: the intraday tab refreshes every 60 s. */
function marketHours(now = new Date()) {
  const p = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(now);
  const v = (t: string) => p.find((x) => x.type === t)?.value ?? "";
  const m = Number(v("hour")) * 60 + Number(v("minute"));
  return !["Sat", "Sun"].includes(v("weekday")) && m >= 9 * 60 + 15 && m <= 15 * 60 + 35;
}

export default function Scanner() {
  const [tf, setTf] = useState<"1d" | "15m">("1d");
  const [date, setDate] = useState<string>("");
  const [det, setDet] = useState<string | null>(null);
  const [sort, setSort] = useState<Sort>({ key: "rr", dir: -1 });
  const [d, setD] = useState<AlertsResp | null>(null);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    const q = (day?: string) => `/api/scan/alerts?${new URLSearchParams({ tf, ...(day ? { date: day } : {}), ...(det ? { detector: det } : {}) })}`;
    try {
      let x = await get<AlertsResp>(q(date || undefined));
      // no session picked: the API's default is the latest session of any timeframe (the EOD 1d
      // scan of today runs at 19:45), so fall back to the latest session with alerts on this tab
      if (!date && !det && x.alerts.length === 0)
        for (const day of x.dates.filter((y) => y !== x.session)) {
          const y = await get<AlertsResp>(q(day));
          if (y.alerts.length) { x = y; break; }
        }
      setD(x); setErr("");
    } catch (e) { setErr(String(e)); }
  }, [tf, date, det]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (tf !== "15m") return;
    const iv = setInterval(() => { if (marketHours()) load(); }, 60000);
    return () => clearInterval(iv);
  }, [tf, load]);

  const rows = useMemo(() => {
    const a = [...(d?.alerts ?? [])];
    const k = (x: (typeof a)[number]) => sort.key === "rr" ? (x.rr_r1 ?? -Infinity) : sort.key === "detector" ? x.detector : (x.symbol ?? x.instrument_key);
    return a.sort((p, q) => { const x = k(p), y = k(q); return (x < y ? -1 : x > y ? 1 : 0) * sort.dir; });
  }, [d, sort]);
  const head = (key: Sort["key"], label: string, cls = "") => (
    <th className={`sortable ${cls}`} onClick={() => setSort({ key, dir: sort.key === key ? (sort.dir === 1 ? -1 : 1) : key === "rr" ? -1 : 1 })}
      aria-sort={sort.key === key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
      {label}{sort.key === key ? (sort.dir === 1 ? " ▲" : " ▼") : ""}
    </th>
  );
  const fresh = (iso: string) => tf === "15m" && Date.now() - new Date(iso).getTime() < NEW_MIN * 60000;

  return (
    <div className="scanpage">
      <DecisionLabel label={d?.label} page="Scanner" />
      <div className="row" style={{ justifyContent: "space-between", margin: "10px 0" }}>
        <h2 style={{ margin: 0 }}>Scanner</h2>
        <div className="row" style={{ gap: 8 }}>
          <div className="seg" role="tablist" aria-label="Timeframe">
            <button role="tab" aria-selected={tf === "1d"} className={tf === "1d" ? "sel" : ""} onClick={() => { setTf("1d"); setDate(""); }}>EOD (1d)</button>
            <button role="tab" aria-selected={tf === "15m"} className={tf === "15m" ? "sel" : ""} onClick={() => { setTf("15m"); setDate(""); }}>Intraday (15m)</button>
          </div>
          <select value={date || d?.session || ""} onChange={(e) => setDate(e.target.value)} aria-label="Session">
            {d?.session && !d.dates.includes(d.session) && <option value={d.session}>{d.session}</option>}
            {(d?.dates ?? []).map((x) => <option key={x} value={x}>{x}</option>)}
          </select>
        </div>
      </div>
      <div className="row chips" style={{ marginBottom: 10 }}>
        <button className={`chip ${det === null ? "sel" : ""}`} onClick={() => setDet(null)}>All detectors</button>
        {(d?.detectors ?? []).map((x) => <button key={x} className={`chip ${det === x ? "sel" : ""}`} onClick={() => setDet(det === x ? null : x)}>{x}</button>)}
      </div>
      {err && <p className="muted">{err}</p>}
      {tf === "15m" && <div className="d5note" role="note">{D5_NOTE}</div>}
      <div className="card scanbox">
        {d && rows.length === 0 ? <p className="muted" style={{ margin: 12 }}>No alerts for this session</p> : (
          <div className="scroll">
            <table className="scantable">
              <thead><tr>
                {head("symbol", "Symbol")}{head("detector", "Detector")}<th>TF</th><th>Bar time (IST)</th>
                <th className="num">Entry</th><th className="num">Stop</th><th className="num">R1</th><th className="num">R2</th>
                {head("rr", "RR to R1", "num")}<th className="num">Qty</th><th className="num">₹ risk</th><th className="num">Cost R</th>
                <th>Regime</th><th>Journal</th><th>Outcome</th><th></th>
              </tr></thead>
              <tbody>{rows.map((a) => (
                <tr key={a.id}>
                  <td><a href={`${stockHref(a.instrument_key)}?tf=${a.tf}`}>{a.symbol ?? a.instrument_key}</a>{fresh(a.created_at) && <span className="pill new">new</span>}</td>
                  <td>{a.detector}</td><td>{a.tf}</td><td>{istTime(a.bar_time, a.tf)}</td>
                  <td className="num">{fmt(a.entry_ref)}</td><td className="num">{fmt(a.stop)}</td>
                  <td className="num">{a.r1 === null ? <span className="muted small">no resistance → 2R</span> : fmt(a.r1)}</td>
                  <td className="num">{a.r2 === null ? <span className="muted">–</span> : fmt(a.r2)}</td>
                  <td className={`num ${rrClass(a.rr_r1)}`} title="colour hint only: red < 1.5, amber 1.5–2, green ≥ 2">{a.rr_r1 === null ? "–" : fmt(a.rr_r1)}</td>
                  <td className="num">{a.qty ?? "–"}</td>
                  <td className="num">{a.qty ? fmt((a.entry_ref - a.stop) * a.qty, 0) : "–"}</td>
                  <td className="num">{fmt(a.cost_r)}</td>
                  <td><span className={`pill ${a.context?.regime === "ON" ? "on" : a.context?.regime === "OFF" ? "off" : ""}`}>{a.context?.regime ?? "–"}</span></td>
                  <td><StatusPill status={a.status} /></td>
                  <td><OutcomePill outcome={a.outcome} r={a.r_net} /></td>
                  <td><a className="btn-sm" href={`/scanner/${a.id}`}>Open</a></td>
                </tr>))}
              </tbody>
            </table>
          </div>
        )}
        {!d && !err && <p className="muted" style={{ margin: 12 }}>Loading…</p>}
      </div>
      <p className="muted small">R1 “no resistance → 2R”: no resistance zone above the entry, so the grader uses entry + 2R. RR colours are a hint only, never a filter. ₹ risk = (entry − stop) × qty. {d ? `${d.version} · session ${d.session ?? "–"}` : ""}</p>
    </div>
  );
}
