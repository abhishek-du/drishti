-- P1c (Option B): Drishti's own official daily bars (Upstox V3 historical). Append-only: every
-- fetch is kept; the latest fetch of (key, date) wins; a re-served value is a new row.

CREATE TABLE bar_1d_payload (                -- one historical-candle response
  id bigserial PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES run(run_id),
  instrument_key varchar(64) NOT NULL,
  req_from date NOT NULL,
  req_to date NOT NULL,
  http_status integer NOT NULL,
  sha256 char(64),                           -- of the response body
  n_candles integer NOT NULL DEFAULT 0,
  basis_as_of date NOT NULL,                 -- Upstox serves history split/bonus-adjusted as of
                                             -- the fetch date (Prajna: VENDOR_ADJUSTED basis)
  error text,
  fetched_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ON bar_1d_payload (instrument_key, fetched_at DESC);

CREATE TABLE bar_1d (
  id bigserial PRIMARY KEY,
  payload_id bigint NOT NULL REFERENCES bar_1d_payload(id),
  instrument_key varchar(64) NOT NULL,
  session_date date NOT NULL,
  open numeric(14,4) NOT NULL, high numeric(14,4) NOT NULL, low numeric(14,4) NOT NULL,
  close numeric(14,4) NOT NULL, volume bigint, oi bigint,
  fetched_at timestamptz NOT NULL
);
CREATE INDEX ON bar_1d (instrument_key, session_date, fetched_at DESC);
CREATE INDEX ON bar_1d (session_date);

DO $$ BEGIN
  EXECUTE 'CREATE TRIGGER bar_1d_payload_append_only BEFORE UPDATE OR DELETE ON bar_1d_payload FOR EACH ROW EXECUTE FUNCTION drishti_append_only()';
  EXECUTE 'CREATE TRIGGER bar_1d_append_only BEFORE UPDATE OR DELETE ON bar_1d FOR EACH ROW EXECUTE FUNCTION drishti_append_only()';
END $$;
