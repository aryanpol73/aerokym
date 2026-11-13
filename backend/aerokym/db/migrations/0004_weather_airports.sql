-- M5: weather layer and airport cards.

-- Airports: a few display fields from OurAirports.
ALTER TABLE airports ADD COLUMN city text, ADD COLUMN iata text, ADD COLUMN kind text;
ALTER TABLE runways ADD COLUMN surface text;

-- Keep the provider's decoded report as JSON next to the columns we query on.
-- The plain-language text is produced from it at request time, so improving
-- the wording never needs a re-ingest.
ALTER TABLE metars ADD COLUMN data jsonb;
ALTER TABLE tafs ADD COLUMN data jsonb;

-- SIGMET outlines are usually polygons, but repairing a self-crossing outline
-- can yield a multi-polygon, so the column accepts any geometry.
ALTER TABLE sigmets
    ALTER COLUMN geom TYPE geometry(Geometry, 4326),
    ADD COLUMN fir text,
    ADD COLUMN qualifier text;

ALTER TABLE pireps ADD COLUMN aircraft_type text;

CREATE INDEX metars_latest_idx ON metars (station, obs_time DESC);
