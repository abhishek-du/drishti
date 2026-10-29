-- P1c (Option B): Drishti's own instrument master, and the user's NSE board-meetings CSV
-- (D2: results calendar, downloaded by hand Mon/Thu). Append-only.

ALTER TABLE manual_file DROP CONSTRAINT manual_file_kind_check;
ALTER TABLE manual_file ADD CONSTRAINT manual_file_kind_check CHECK (kind IN
  ('ASM','GSM','DELISTED','EQUITY_L','SYMBOL_CHANGE','NAME_CHANGE','CONTRACT_NOTE',
   'BOARD_MEETINGS'));

CREATE TABLE instrument_master_file (        -- one download of the Upstox public NSE master
  run_id uuid PRIMARY KEY REFERENCES run(run_id),
  url text NOT NULL,
  sha256 char(64) NOT NULL,
  rows_total integer NOT NULL,               -- every NSE row in the file
  rows_kept integer NOT NULL,                -- NSE_EQ + NSE_INDEX
  fetched_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE instrument_event (              -- the master as events: state = fold of events
  id bigserial PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES instrument_master_file(run_id),
  instrument_key varchar(64) NOT NULL,
  event varchar(8) NOT NULL CHECK (event IN ('ADDED','CHANGED','REMOVED')),
  attrs jsonb NOT NULL,                      -- segment, instrument_type (series), symbol, name,
                                             -- isin, lot, tick, exchange_token ... (REMOVED: last)
  changed jsonb NOT NULL DEFAULT '{}',       -- CHANGED: {field: [old, new]}
  knowable_at timestamptz NOT NULL           -- = fetched_at of the file
);
CREATE INDEX ON instrument_event (instrument_key, knowable_at);

DO $$ BEGIN
  EXECUTE 'CREATE TRIGGER instrument_master_file_append_only BEFORE UPDATE OR DELETE ON instrument_master_file FOR EACH ROW EXECUTE FUNCTION drishti_append_only()';
  EXECUTE 'CREATE TRIGGER instrument_event_append_only BEFORE UPDATE OR DELETE ON instrument_event FOR EACH ROW EXECUTE FUNCTION drishti_append_only()';
END $$;
