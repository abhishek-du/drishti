-- Drishti P2: Scout watchlist log. Append-only (trigger drishti_append_only from 0001).

CREATE TABLE watchlist_run (                 -- one Scout run on session T
  run_id uuid PRIMARY KEY REFERENCES run(run_id),
  session_date date NOT NULL,
  version varchar(32) NOT NULL,              -- scout-b-v1
  params jsonb NOT NULL,                     -- every threshold used
  bar_source varchar(16) NOT NULL CHECK (bar_source IN ('provisional','official')),
  regime jsonb NOT NULL,                     -- Nifty 500 vs SMA200, India VIX, status
  surveillance jsonb NOT NULL,               -- manual.surveillance_freshness() at run time
  counts jsonb NOT NULL,                     -- funnel: universe -> each rule -> listed
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ON watchlist_run (session_date, created_at DESC);

CREATE TABLE watchlist_event (               -- the list of T and its changes against the last run
  id bigserial PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES watchlist_run(run_id),
  session_date date NOT NULL,
  instrument_key varchar(64) NOT NULL,
  symbol varchar(64),
  event varchar(8) NOT NULL CHECK (event IN ('ADDED','KEPT','DROPPED')),
  rank integer,                              -- by RS score; null for DROPPED
  rs_score numeric(10,4),
  metrics jsonb NOT NULL DEFAULT '{}',       -- close, ATR %, 52w distance, SMA levels ...
  flags jsonb NOT NULL DEFAULT '{}'          -- results ahead, ASM/GSM, candle flags of T
);
CREATE INDEX ON watchlist_event (session_date, instrument_key);
CREATE INDEX ON watchlist_event (run_id);

CREATE TRIGGER watchlist_run_append_only BEFORE UPDATE OR DELETE ON watchlist_run
  FOR EACH ROW EXECUTE FUNCTION drishti_append_only();
CREATE TRIGGER watchlist_event_append_only BEFORE UPDATE OR DELETE ON watchlist_event
  FOR EACH ROW EXECUTE FUNCTION drishti_append_only();
