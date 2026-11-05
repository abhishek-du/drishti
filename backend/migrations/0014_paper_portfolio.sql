-- Paper portfolio pf-v1, Phase 1 ledger (user 2026-11-05; docs/PREREG_PAPER_PORTFOLIO.md).
-- PAPER ONLY: nothing here is a real order. Logs are append-only; pf_portfolio and pf_position
-- are the only mutable state (DEVIATIONS D7) and every position change is also logged in
-- pf_position_event. Times are timestamptz (shown in IST).

CREATE TABLE pf_portfolio (                       -- MUTABLE (D7): the live snapshot
  id varchar(16) PRIMARY KEY,                     -- main / placebo / benchmark / obs_all / obs_placebo
  kind varchar(16) NOT NULL CHECK (kind IN ('main', 'placebo', 'benchmark', 'observational')),
  label text NOT NULL,
  research_only boolean NOT NULL DEFAULT false,
  version varchar(16) NOT NULL,                   -- pf-v1
  start_date date NOT NULL,
  starting_capital numeric(16,2) NOT NULL,
  cash numeric(16,2) NOT NULL,
  holdings_value numeric(16,2) NOT NULL DEFAULT 0,
  equity numeric(16,2) NOT NULL,
  peak_equity numeric(16,2) NOT NULL,
  meta jsonb NOT NULL DEFAULT '{}',               -- benchmark: index key, base close/date
  as_of timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE pf_decision (                        -- every candidate seen, taken or skipped
  id bigserial PRIMARY KEY,
  portfolio_id varchar(16) NOT NULL REFERENCES pf_portfolio(id),
  ts timestamptz NOT NULL,
  session_date date NOT NULL,
  instrument_key varchar(64) NOT NULL, symbol varchar(64),
  candidate_from varchar(8) NOT NULL CHECK (candidate_from IN ('scanner', 'radar', 'manual')),
  source_alert_id bigint REFERENCES scan_alert(id),
  tf varchar(4),
  decision varchar(8) NOT NULL CHECK (decision IN ('take', 'skip', 'observe')),
  gate_results jsonb NOT NULL DEFAULT '{}',       -- {regime: {...}, odds: {...}, risk: {...}, results: {...}}
  reason_code varchar(48) NOT NULL,               -- SKIP_ODDS_FAIL, ENTRY_BREAKOUT_52W_ODDS_PASS ...
  why text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ON pf_decision (portfolio_id, session_date);

CREATE TABLE pf_order (
  id bigserial PRIMARY KEY,
  portfolio_id varchar(16) NOT NULL REFERENCES pf_portfolio(id),
  book varchar(8) NOT NULL CHECK (book IN ('swing', 'intraday')),
  ts timestamptz NOT NULL,
  instrument_key varchar(64) NOT NULL, symbol varchar(64),
  side varchar(4) NOT NULL CHECK (side IN ('BUY', 'SELL')),
  product varchar(3) NOT NULL CHECK (product IN ('CNC', 'MIS')),
  order_type varchar(6) NOT NULL CHECK (order_type IN ('MARKET', 'LIMIT', 'SL', 'SL-M')),
  qty integer NOT NULL CHECK (qty > 0),
  limit_price numeric(14,4), trigger_price numeric(14,4),
  status varchar(10) NOT NULL CHECK (status IN ('filled', 'cancelled', 'rejected', 'pending')),
  source_alert_id bigint REFERENCES scan_alert(id),
  decision_id bigint REFERENCES pf_decision(id),
  reason_code varchar(48) NOT NULL, reason_text text NOT NULL,
  stop numeric(14,4), target numeric(14,4),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ON pf_order (portfolio_id, ts);

CREATE TABLE pf_fill (
  id bigserial PRIMARY KEY,
  order_id bigint NOT NULL REFERENCES pf_order(id),
  portfolio_id varchar(16) NOT NULL REFERENCES pf_portfolio(id),
  ts timestamptz NOT NULL,
  instrument_key varchar(64) NOT NULL, symbol varchar(64),
  side varchar(4) NOT NULL CHECK (side IN ('BUY', 'SELL')),
  product varchar(3) NOT NULL,
  qty integer NOT NULL CHECK (qty > 0),
  price numeric(14,4) NOT NULL,
  brokerage numeric(12,2) NOT NULL, stt numeric(12,2) NOT NULL,
  exchange_fee numeric(12,2) NOT NULL, gst numeric(12,2) NOT NULL,
  stamp numeric(12,2) NOT NULL, sebi_fee numeric(12,2) NOT NULL, dp numeric(12,2) NOT NULL,
  slippage numeric(12,2) NOT NULL,                -- modelled, base bps of the turnover
  total_costs numeric(12,2) NOT NULL,
  net_cash numeric(16,2) NOT NULL,                -- signed cash change (buy < 0, sell > 0)
  cost_model text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ON pf_fill (portfolio_id, ts);

CREATE TABLE pf_position (                        -- MUTABLE (D7): open positions only
  id bigserial PRIMARY KEY,
  portfolio_id varchar(16) NOT NULL REFERENCES pf_portfolio(id),
  book varchar(8) NOT NULL CHECK (book IN ('swing', 'intraday')),
  instrument_key varchar(64) NOT NULL, symbol varchar(64),
  product varchar(3) NOT NULL,
  qty integer NOT NULL CHECK (qty > 0),
  avg_price numeric(14,4) NOT NULL,
  entry_costs numeric(12,2) NOT NULL,             -- buy-side costs of the open qty (cost basis)
  opened_ts timestamptz NOT NULL,
  last_price numeric(14,4) NOT NULL, last_price_at timestamptz NOT NULL,
  unrealized_pnl numeric(16,2) NOT NULL,          -- (last - avg) * qty - entry_costs
  stop numeric(14,4), target numeric(14,4),
  source_alert_id bigint REFERENCES scan_alert(id),
  reason_in text,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (portfolio_id, book, instrument_key)
);

CREATE TABLE pf_position_event (                  -- append-only log of every position change
  id bigserial PRIMARY KEY,
  portfolio_id varchar(16) NOT NULL REFERENCES pf_portfolio(id),
  book varchar(8) NOT NULL,
  instrument_key varchar(64) NOT NULL,
  event varchar(6) NOT NULL CHECK (event IN ('OPEN', 'ADD', 'REDUCE', 'CLOSE', 'MARK')),
  fill_id bigint REFERENCES pf_fill(id),
  qty_before integer NOT NULL, qty_after integer NOT NULL,
  avg_before numeric(14,4), avg_after numeric(14,4),
  price numeric(14,4) NOT NULL,
  ts timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ON pf_position_event (portfolio_id, instrument_key, ts);

CREATE TABLE pf_trade (                           -- closed round trips (or the closed part)
  id bigserial PRIMARY KEY,
  portfolio_id varchar(16) NOT NULL REFERENCES pf_portfolio(id),
  book varchar(8) NOT NULL,
  instrument_key varchar(64) NOT NULL, symbol varchar(64),
  product varchar(3) NOT NULL,
  qty integer NOT NULL,
  entry_ts timestamptz NOT NULL, exit_ts timestamptz NOT NULL,
  entry_price numeric(14,4) NOT NULL, exit_price numeric(14,4) NOT NULL,
  gross_pnl numeric(16,2) NOT NULL, costs numeric(12,2) NOT NULL, net_pnl numeric(16,2) NOT NULL,
  stop numeric(14,4),
  r_multiple numeric(10,4),                       -- net_pnl / ((entry - stop) * qty)
  holding_sessions integer,
  reason_in text, reason_out text,
  source_alert_id bigint REFERENCES scan_alert(id),
  exit_order_id bigint REFERENCES pf_order(id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ON pf_trade (portfolio_id, exit_ts);

CREATE TABLE pf_equity (                          -- daily curve; the newest row per key wins
  id bigserial PRIMARY KEY,
  portfolio_id varchar(16) NOT NULL REFERENCES pf_portfolio(id),
  book varchar(8) NOT NULL CHECK (book IN ('all', 'swing', 'intraday')),
  date date NOT NULL,
  cash numeric(16,2), holdings_value numeric(16,2) NOT NULL, equity numeric(16,2) NOT NULL,
  daily_pnl numeric(16,2), drawdown numeric(8,5),
  benchmark_equity numeric(16,2), placebo_equity numeric(16,2),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ON pf_equity (portfolio_id, book, date, created_at DESC);

CREATE VIEW pf_equity_latest AS
SELECT DISTINCT ON (portfolio_id, book, date) * FROM pf_equity
ORDER BY portfolio_id, book, date, created_at DESC, id DESC;

CREATE TRIGGER pf_decision_append_only BEFORE UPDATE OR DELETE ON pf_decision
  FOR EACH ROW EXECUTE FUNCTION drishti_append_only();
CREATE TRIGGER pf_order_append_only BEFORE UPDATE OR DELETE ON pf_order
  FOR EACH ROW EXECUTE FUNCTION drishti_append_only();
CREATE TRIGGER pf_fill_append_only BEFORE UPDATE OR DELETE ON pf_fill
  FOR EACH ROW EXECUTE FUNCTION drishti_append_only();
CREATE TRIGGER pf_position_event_append_only BEFORE UPDATE OR DELETE ON pf_position_event
  FOR EACH ROW EXECUTE FUNCTION drishti_append_only();
CREATE TRIGGER pf_trade_append_only BEFORE UPDATE OR DELETE ON pf_trade
  FOR EACH ROW EXECUTE FUNCTION drishti_append_only();
CREATE TRIGGER pf_equity_append_only BEFORE UPDATE OR DELETE ON pf_equity
  FOR EACH ROW EXECUTE FUNCTION drishti_append_only();

GRANT SELECT ON pf_equity_latest TO drishti_app;
