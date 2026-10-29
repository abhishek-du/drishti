"use client";
// Symbol search of the chart's top bar: the latest watchlist when empty, the instrument master
// (/api/search) when typing. Opens the stock page on the same timeframe.
import { useEffect, useState } from "react";
import { get, stockHref } from "../api";

type Hit = { instrument_key: string; trading_symbol: string; name: string };
type Wl = { rows: { instrument_key: string; symbol: string }[] };

export default function SymbolSearch({ current, tf }: { current: string; tf: string }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [wl, setWl] = useState<Hit[] | null>(null);
  useEffect(() => {
    if (!open || wl) return;
    get<Wl>("/api/watchlist").then((w) => setWl(w.rows.map((r) => ({ instrument_key: r.instrument_key, trading_symbol: r.symbol, name: "watchlist" })))).catch(() => setWl([]));
  }, [open, wl]);
  useEffect(() => {
    if (q.trim().length < 1) { setHits([]); return; }
    const t = setTimeout(() => get<Hit[]>(`/api/search?q=${encodeURIComponent(q.trim())}`).then(setHits).catch(() => setHits([])), 200);
    return () => clearTimeout(t);
  }, [q]);
  const go = (k: string) => { location.href = `${stockHref(k)}?tf=${tf}`; };
  const list = q.trim() ? hits : (wl ?? []);
  return (
    <div className="kc-sym">
      <button className="kc-symbtn" onClick={() => setOpen(!open)} title="Symbol search">🔍 <b>{current}</b></button>
      {open && (
        <div className="kc-pop kc-sympop">
          <input autoFocus placeholder="Symbol or name…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search symbol"
            onKeyDown={(e) => { if (e.key === "Enter" && list[0]) go(list[0].instrument_key); if (e.key === "Escape") setOpen(false); }} />
          {!q.trim() && <div className="muted small">Latest watchlist</div>}
          <ul>
            {list.slice(0, 40).map((h) => (
              <li key={h.instrument_key} onClick={() => go(h.instrument_key)}><b>{h.trading_symbol}</b> <span className="muted">{h.name}</span></li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
