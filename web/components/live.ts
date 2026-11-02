import { get } from "./api";

// The live feed comes straight from the API's WebSocket (Next's rewrites do not proxy
// WebSockets). Local only: the API is NEXT_PUBLIC_DRISHTI_API, else this host on port 8700.
export const LIVE_LABEL = "LIVE VIEW — not used by drishti-swing-v1";
export type LiveQuote = { ltp: number | null; cp: number | null; vwap: number | null; vtt: number | null; chg_pct: number | null; last_trade: string | null; ticks: number };
export type LiveMsg = { label: string; summary: { updated_at: string | null; keys: Record<string, LiveQuote> }; candles?: LiveCandle[] };
export type LiveCandle = { time: number; open: number; high: number; low: number; close: number; volume: number; vwap: number | null };

const API = process.env.NEXT_PUBLIC_DRISHTI_API;
export const wsBase = () => {
  if (typeof window === "undefined") return "";
  const host = window.location.hostname;

  // 1. Public ELB / CDN domains (cisinlive.com): ELB proxy currently returns 404 on WS upgrade
  // so fallback directly to HTTP polling to avoid repeated 404 console spam.
  if (host.includes("cisinlive.com") || host.includes("elb")) {
    return "";
  }

  const isHttps = window.location.protocol === "https:";
  const proto = isHttps ? "wss" : "ws";

  // 2. Localhost or 127.0.0.1
  if (host === "127.0.0.1" || host === "localhost") {
    const p = API ? (new URL(API).port || "8700") : "8700";
    return `ws://127.0.0.1:${p}`;
  }

  // 3. Local LAN IP (e.g. 192.168.4.22)
  const p = API ? (new URL(API).port || "8700") : "8700";
  return `${proto}://${host}:${p}`;
};

export function openLive(onMsg: (m: LiveMsg) => void, key?: string, tf = 1): () => void {
  let ws: WebSocket | null = null;
  let stop = false;
  let retry: ReturnType<typeof setTimeout> | undefined;
  let pollTimer: ReturnType<typeof setInterval> | undefined;
  let wsConnected = false;

  const poll = async () => {
    if (stop || wsConnected) return;
    try {
      const summaryPromise = get<Record<string, unknown>>("/api/live/summary");
      const candlesPromise = key
        ? get<{ candles: LiveCandle[] }>(`/api/live/candles?key=${encodeURIComponent(key)}&tf=${tf}`)
        : Promise.resolve(null);
      const [sum, can] = await Promise.all([summaryPromise, candlesPromise]);
      if (!stop && !wsConnected && sum) {
        onMsg({
          label: LIVE_LABEL,
          summary: sum as LiveMsg["summary"],
          candles: can?.candles,
        });
      }
    } catch {
      /* ignore polling error */
    }
  };

  const startPolling = () => {
    if (pollTimer) return;
    poll();
    pollTimer = setInterval(poll, 1500);
  };

  const stopPolling = () => {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = undefined;
    }
  };

  const connect = () => {
    try {
      const base = wsBase();
      if (!base) {
        startPolling();
        return;
      }
      const url = `${base}/api/live/ws?tf=${tf}${key ? `&key=${encodeURIComponent(key)}` : ""}`;
      ws = new WebSocket(url);
      ws.onopen = () => {
        wsConnected = true;
        stopPolling();
      };
      ws.onmessage = (e) => {
        try {
          onMsg(JSON.parse(e.data));
        } catch {
          /* ignore a bad frame */
        }
      };
      ws.onclose = () => {
        wsConnected = false;
        startPolling();
        if (!stop) retry = setTimeout(connect, 5000);
      };
      ws.onerror = () => {
        wsConnected = false;
        startPolling();
        try {
          ws?.close();
        } catch {
          /* ignore */
        }
      };
    } catch {
      wsConnected = false;
      startPolling();
      if (!stop) retry = setTimeout(connect, 5000);
    }
  };

  connect();
  startPolling();

  return () => {
    stop = true;
    stopPolling();
    clearTimeout(retry);
    try {
      ws?.close();
    } catch {
      /* ignore */
    }
  };
}

export const stale = (iso: string | null | undefined, s = 30) => !iso || Date.now() - new Date(iso).getTime() > s * 1000;
