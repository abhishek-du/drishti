-- Live Radar (user 2026-11-04): stocks that newly meet Scout's criteria intraday, on a partial
-- daily bar built up to the last closed 15m bar. Separate from the EOD watchlist (which is not
-- changed). "Intraday radar - decision support, not validated edge". Append-only.

CREATE TABLE radar_event (
  id bigserial PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES run(run_id),
  session_date date NOT NULL,
  bar_time timestamptz NOT NULL,             -- start of the last closed 15m bar used
  instrument_key varchar(64) NOT NULL,
  symbol varchar(64),
  version varchar(32) NOT NULL,              -- radar-v1
  event varchar(8) NOT NULL CHECK (event IN ('ADDED')),
  reason jsonb NOT NULL,                     -- the criteria that turned true since T-1
  criteria jsonb NOT NULL,                   -- {name: {"t1": bool, "now": bool}}
  metrics jsonb NOT NULL,                    -- close, rs_score, cut, sma50/200, hi52, atr_pct ...
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (session_date, instrument_key)
);
CREATE INDEX ON radar_event (session_date, bar_time);

CREATE TRIGGER radar_event_append_only BEFORE UPDATE OR DELETE ON radar_event
  FOR EACH ROW EXECUTE FUNCTION drishti_append_only();
