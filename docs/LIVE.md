# Live view and tick recorder

**LIVE VIEW: not used by drishti-swing-v1, its signals, Scout or the forward test.**

## What runs

- **Recorder:** `drishti/live/recorder.py` opens **one** Upstox V3 full-mode WebSocket for the latest watchlist (≤ 300 keys) plus Nifty 50, Nifty 500 and India VIX.
- **Window:**
  - **09:21–15:35 IST under decision A.** Prajna's pre-open recorder holds both connections 08:55–09:20, so Drishti starts one minute after it ends.
  - 09:15–15:35 after the switch.
  - Regular sessions only.
- **Token:** read only (Prajna's file until the switch).
- **Outputs:**
  - ticks: `/windows/drishti_data/ticks/<day>/ticks_HHMM.zst`, the raw protobuf frames with their receive time;
  - 1m bars: `/windows/drishti_data/bars_1m/<day>/*.parquet`;
  - live snapshot: `/dev/shm/drishti_live/`;
  - health: `backend/var/run/live_health.json`.
- **Retention:** 30 days. **Disk guard:** below 10 GB free, tick writing stops and an alert is raised (`DRISHTI_LIVE_ALERT` in `cron.log`, and on the Ops page).
- **Web:**
  - the stock page's "Live intraday" tab (`?tab=live`): 1m/5m candles, exchange VWAP (atp) and volume;
  - live LTP / % / VWAP columns on the watchlist;
  - the WS health card on the Ops page.

## First measurement (2026-10-29, 15:17–15:29, 52 keys, a short test near the close)

| Measure | Value |
|---|---|
| Frames | 4,414 (all read back from the archive) |
| Raw frame bytes | 6.02 MB |
| **On disk (zstd)** | **0.63 MB** (×9.6), i.e. ≈ 1.05 KB per key-minute |
| 1m bars written | 590 rows (2 Parquet files) |
| Reconnects | 1 (`keepalive ping timeout`). Ping interval/timeout raised from 10/10 s to 20/20 s |

**Extrapolation** (to be replaced by the first full-day measurement on 2026-10-30):

| Keys | Per day (~375 min) | 30 days | 60 days | 1 year |
|---|---|---|---|---|
| 52 (today's watchlist) | ~0.02 GB | ~0.6 GB | ~1.2 GB | ~5 GB |
| 300 | ~0.12 GB | ~3.6 GB | ~7 GB | ~30 GB |

The open is busier than this test window, so expect up to ~1.5× more. Either way this is far below the plan's earlier estimate of ~1 GB/day for 300 keys. **Proposal, after the first full-day number:** retention of **60 days or more is affordable on /windows now** (63 GB free), without waiting for cleanup 3.1. The user decides.
