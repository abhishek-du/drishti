"use client";
import { useEffect, useState } from "react";
import { get, stockHref } from "./api";

type Hit = { instrument_key: string; trading_symbol: string; name: string };

export default function Search() {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  useEffect(() => {
    if (q.trim().length < 2) { setHits([]); return; }
    const t = setTimeout(() => get<Hit[]>(`/api/search?q=${encodeURIComponent(q.trim())}`).then(setHits).catch(() => setHits([])), 200);
    return () => clearTimeout(t);
  }, [q]);
  return (
    <div className="search">
      <input placeholder="Search symbol…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search symbol" />
      {hits.length > 0 && (
        <ul>
          {hits.map((h) => (
            <li key={h.instrument_key} onClick={() => { location.href = stockHref(h.instrument_key); }}>
              <b>{h.trading_symbol}</b> <span className="muted">{h.name}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
