-- Drishti P1 base tables. Append-only: UPDATE/DELETE raise (trigger drishti_append_only).

CREATE OR REPLACE FUNCTION drishti_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'append-only table %', TG_TABLE_NAME;
END $$;

CREATE TABLE run (
  run_id uuid PRIMARY KEY,
  kind varchar(32) NOT NULL,
  session_date date,
  started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  code_sha varchar(40) NOT NULL,
  params jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE run_end (                       -- the outcome of a run (separate: run stays append-only)
  run_id uuid PRIMARY KEY REFERENCES run(run_id),
  finished_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status varchar(16) NOT NULL CHECK (status IN ('OK','PARTIAL','FAILED','NOOP')),
  counts jsonb NOT NULL DEFAULT '{}',
  error text
);

CREATE TABLE trading_holiday (               -- Upstox /v2/market/holidays, every fetch kept
  id bigserial PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES run(run_id),
  holiday_date date NOT NULL,
  description text,
  holiday_type varchar(32) NOT NULL,
  nse_closed boolean NOT NULL,
  nse_open_from timestamptz,                 -- SPECIAL_TIMING sessions
  nse_open_to timestamptz,
  fetched_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ON trading_holiday (holiday_date, fetched_at DESC);

CREATE TABLE index_bar (                     -- NSE index daily bars (Nifty 500, sectors, VIX ...)
  id bigserial PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES run(run_id),
  index_key varchar(64) NOT NULL,
  session_date date NOT NULL,
  open numeric(14,4), high numeric(14,4), low numeric(14,4), close numeric(14,4) NOT NULL,
  source varchar(16) NOT NULL CHECK (source IN ('UPSTOX_HIST','UPSTOX_QUOTE')),
  fetched_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ON index_bar (index_key, session_date, fetched_at DESC);

CREATE TABLE eod_quote (                     -- provisional daily bar of T from the 15:40 quote
  id bigserial PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES run(run_id),
  instrument_key varchar(64) NOT NULL,
  session_date date NOT NULL,
  open numeric(14,4), high numeric(14,4), low numeric(14,4), close numeric(14,4),
  last_price numeric(14,4), volume bigint,
  quote_ts timestamptz,
  fetched_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ON eod_quote (session_date, instrument_key, fetched_at DESC);

CREATE TABLE manual_file (                   -- user-downloaded files in drishti_data/manual/
  file_id bigserial PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES run(run_id),
  kind varchar(24) NOT NULL CHECK (kind IN ('ASM','GSM','DELISTED','EQUITY_L','SYMBOL_CHANGE',
                                            'NAME_CHANGE','CONTRACT_NOTE')),
  path text NOT NULL,
  file_date date NOT NULL,                   -- knowable from (PIT): the download date
  sha256 char(64) NOT NULL UNIQUE,
  row_count integer NOT NULL,
  loaded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE manual_row (
  id bigserial PRIMARY KEY,
  file_id bigint NOT NULL REFERENCES manual_file(file_id),
  symbol varchar(64),
  isin varchar(12),
  stage text,                                -- ASM/GSM stage, delisting type, series ...
  event_date date,                           -- delisting / listing / change date
  extra jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX ON manual_row (file_id);
CREATE INDEX ON manual_row (isin);

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['run','run_end','trading_holiday','index_bar','eod_quote',
                           'manual_file','manual_row'] LOOP
    EXECUTE format('CREATE TRIGGER %I_append_only BEFORE UPDATE OR DELETE ON %I
                    FOR EACH ROW EXECUTE FUNCTION drishti_append_only()', t, t);
  END LOOP;
END $$;
