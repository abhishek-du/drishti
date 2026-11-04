-- Time Machine odds per plan (Track B's PROPOSED_DDL, odds-1d-v1; user 2026-11-04), filled by
-- the nightly odds job (drishti.scanner.odds_job) for the day's 1d scanner alerts.
-- RESEARCH / SIMULATION, NOT a signal. Append-only: the newest row per (alert_id, basis) wins.
-- Added to the proposal: label, caveat (shown with every result), data_as_of (the knowledge
-- cut of the bars used).
CREATE TABLE odds_result (
  id bigserial PRIMARY KEY,
  alert_id bigint REFERENCES scan_alert(id),     -- NULL for a manual plan
  basis varchar(8) NOT NULL CHECK (basis IN ('alert', 'took', 'manual')),
  instrument_key varchar(64) NOT NULL, symbol varchar(64),
  as_of date NOT NULL, tf varchar(4) NOT NULL DEFAULT '1d',
  entry numeric(14,4) NOT NULL, stop numeric(14,4) NOT NULL, target numeric(14,4) NOT NULL,
  horizon integer NOT NULL, qty integer NOT NULL,
  version varchar(16) NOT NULL, seed bigint NOT NULL, code_sha varchar(40),
  params jsonb NOT NULL,                         -- n_paths, block, lookback, embargo, slip_r, bps
  pool jsonb NOT NULL,                           -- first/last pool date, n days
  mc jsonb NOT NULL, replay jsonb NOT NULL,      -- target/stop/neither/green %, R quantiles
  p_target_mc numeric(6,4),                      -- for the calibration check
  breakeven numeric(6,4), verdict text NOT NULL,
  label text, caveat text,
  data_as_of timestamptz,                        -- bars and CA factors knowable before this
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ON odds_result (alert_id, basis, created_at DESC);

CREATE TRIGGER odds_result_append_only BEFORE UPDATE OR DELETE ON odds_result
  FOR EACH ROW EXECUTE FUNCTION drishti_append_only();
