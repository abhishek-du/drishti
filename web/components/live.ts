// The live feed comes straight from the API's WebSocket (Next's rewrites do not proxy
// WebSockets). Local only: the API listens on 127.0.0.1:8700.
export const LIVE_LABEL = "LIVE VIEW — not used by drishti-swing-v1";
export type LiveQuote = { ltp: number | null; cp: number | null; vwap: number | null; vtt: number | null; chg_pct: number | null; last_trade: string | null; ticks: number };
export type LiveMsg = { label: string; summary: { updated_at: string | null; keys: Record<string, LiveQuote> }; candles?: LiveCandle[] };
export type LiveCandle = { time: number; open: number; high: number; low: number; close: number; volume: number; vwap: number | null };

export function openLive(onMsg: (m: LiveMsg) => void, key?: string, tf = 1): () => void {
  let ws: WebSocket | null = null;
  let stop = false;
  let retry: ReturnType<typeof setTimeout> | undefined;
  const url = `ws://${location.hostname}:8700/api/live/ws?tf=${tf}${key ? `&key=${encodeURIComponent(key)}` : ""}`;
  const connect = () => {
    ws = new WebSocket(url);
    ws.onmessage = (e) => { try { onMsg(JSON.parse(e.data)); } catch { /* ignore a bad frame */ } };
    ws.onclose = () => { if (!stop) retry = setTimeout(connect, 3000); };
  };
  connect();
  return () => { stop = true; clearTimeout(retry); ws?.close(); };
}

export const stale = (iso: string | null | undefined, s = 30) => !iso || Date.now() - new Date(iso).getTime() > s * 1000;
