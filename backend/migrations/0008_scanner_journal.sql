-- Decision-support SCANNER + paper JOURNAL for the user's own discretionary trading (user,
-- 2026-11-03). PAPER ONLY, no orders. Separate from drishti-swing-v1 and the frozen research.
-- "Decision support - detections are NOT validated edges. Your call. Paper only."

CREATE TABLE scan_alert (                    -- one detector hit on one closed bar (append-only)
  id bigserial PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES run(run_id),
  session_date date NOT NULL,
  tf varchar(4) NOT NULL CHECK (tf IN ('1d', '15m')),
  bar_time timestamptz NOT NULL,             -- the detection bar (closed)
  instrument_key varchar(64) NOT NULL,
  symbol varchar(64),
  detector varchar(32) NOT NULL,             -- HAMMER_CONFIRM / BREAKOUT_52W / SUPPORT_REVERSAL / EMA20_PULLBACK
  version varchar(16) NOT NULL,              -- scan-v1
  entry_ref numeric(14,4) NOT NULL,          -- buy-stop reference for the next bar
  stop numeric(14,4) NOT NULL,
  r1 numeric(14,4), r2 numeric(14,4),        -- nearest / next resistance (band bottoms); NULL = none
  rr_r1 numeric(10,4),
  qty integer NOT NULL, capital numeric(16,2) NOT NULL, risk_pct numeric(6,3) NOT NULL,
  cost_r numeric(10,4),                      -- estimated round-trip cost in R (to R1, or 2R)
  atr numeric(14,4),
  context jsonb NOT NULL DEFAULT '{}',       -- trend, RSI, regime, spec_gate, results ahead ...
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (instrument_key, detector, tf, bar_time)
);
CREATE INDEX ON scan_alert (session_date, tf);

CREATE TABLE journal_entry (                 -- the user's decision on an alert (editable)
  alert_id bigint PRIMARY KEY REFERENCES scan_alert(id),
  status varchar(10) NOT NULL CHECK (status IN ('watching', 'took', 'skipped')),
  actual_entry numeric(14,4), actual_qty integer,
  notes text,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE journal_event (                 -- every journal change, as an audit trail (append-only)
  id bigserial PRIMARY KEY,
  alert_id bigint NOT NULL REFERENCES scan_alert(id),
  change jsonb NOT NULL,
  at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE journal_outcome (               -- triple-barrier grade; the latest per (alert, basis) wins
  id bigserial PRIMARY KEY,
  alert_id bigint NOT NULL REFERENCES scan_alert(id),
  run_id uuid NOT NULL REFERENCES run(run_id),
  basis varchar(8) NOT NULL CHECK (basis IN ('alert', 'took', 'placebo')),
  outcome varchar(8) NOT NULL CHECK (outcome IN ('R1', 'STOP', 'TIMEOUT', 'OPEN', 'NO_FILL')),
  entry_used numeric(14,4), exit_price numeric(14,4), exit_bar timestamptz,
  target_used numeric(14,4), r_gross numeric(10,4), r_net numeric(10,4),
  bars_used integer, seed bigint,
  graded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ON journal_outcome (alert_id, basis, graded_at DESC);

DO $$ BEGIN
  EXECUTE 'CREATE TRIGGER scan_alert_append_only BEFORE UPDATE OR DELETE ON scan_alert FOR EACH ROW EXECUTE FUNCTION drishti_append_only()';
  EXECUTE 'CREATE TRIGGER journal_event_append_only BEFORE UPDATE OR DELETE ON journal_event FOR EACH ROW EXECUTE FUNCTION drishti_append_only()';
  EXECUTE 'CREATE TRIGGER journal_outcome_append_only BEFORE UPDATE OR DELETE ON journal_outcome FOR EACH ROW EXECUTE FUNCTION drishti_append_only()';
END $$;
