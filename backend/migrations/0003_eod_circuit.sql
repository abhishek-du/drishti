-- Price-band inputs on the 15:40 quote snapshot (Upstox full quote). Older rows stay NULL.
ALTER TABLE eod_quote ADD COLUMN upper_circuit numeric(14,4);
ALTER TABLE eod_quote ADD COLUMN lower_circuit numeric(14,4);
ALTER TABLE eod_quote ADD COLUMN net_change numeric(14,4);   -- last_price - previous close
