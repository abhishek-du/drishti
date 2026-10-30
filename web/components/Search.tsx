"use client";

import { useEffect, useRef, useState } from "react";
import { get, stockHref } from "./api";

type Hit = { instrument_key: string; trading_symbol: string; name: string };

export default function Search() {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (q.trim().length < 2) {
      setHits([]);
      setOpen(false);
      return;
    }
    const t = setTimeout(() => {
      get<Hit[]>(`/api/search?q=${encodeURIComponent(q.trim())}`)
        .then((res) => {
          setHits(res);
          setOpen(res.length > 0);
        })
        .catch(() => {
          setHits([]);
          setOpen(false);
        });
    }, 180);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        inputRef.current?.focus();
      }
      if (e.key === "Escape") {
        setOpen(false);
      }
    };
    const onClick = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, []);

  return (
    <div className="search-wrap" ref={wrapRef}>
      <div className="search-input-box">
        <svg className="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <circle cx="11" cy="11" r="8" />
          <path d="m21 21-4.3-4.3" />
        </svg>
        <input
          ref={inputRef}
          placeholder="Search symbol (e.g. RELIANCE)..."
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onFocus={() => { if (hits.length > 0) setOpen(true); }}
          aria-label="Search symbol"
          className="search-field"
        />
        <span className="search-shortcut">⌘K</span>
      </div>
      {open && hits.length > 0 && (
        <ul className="search-results">
          {hits.map((h) => (
            <li
              key={h.instrument_key}
              onClick={() => {
                location.href = stockHref(h.instrument_key);
              }}
            >
              <span className="sym">{h.trading_symbol}</span>
              <span className="name">{h.name}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
