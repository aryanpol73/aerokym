-- Bookkeeping for the adsb.lol trace sampler: when did we last spend a request
-- on this aircraft? Stops us from fetching the same two hours twice.
CREATE TABLE trace_fetches (
    icao24      text PRIMARY KEY,
    fetched_at  timestamptz NOT NULL,
    n_positions integer NOT NULL DEFAULT 0
);
