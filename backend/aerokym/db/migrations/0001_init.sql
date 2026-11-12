-- Aerokym schema v1.
-- One Postgres 16 with TimescaleDB (time series), PostGIS (geometry) and h3
-- (hex cells). Raw positions are big and short-lived; everything derived from
-- them is small and kept.

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS h3;

-- Raw ADS-B samples ---------------------------------------------------------
CREATE TABLE positions (
    time          timestamptz      NOT NULL,
    icao24        text             NOT NULL,
    lat           double precision NOT NULL,
    lon           double precision NOT NULL,
    baro_alt_ft   integer,
    geom_alt_ft   integer,
    baro_rate_fpm integer,
    geom_rate_fpm integer,
    gs_kt         real,
    track_deg     real,
    on_ground     boolean          NOT NULL DEFAULT false,
    source        text             NOT NULL,
    -- The primary key doubles as the (icao24, time) index the detector reads
    -- by, and makes re-ingesting the same sample a no-op (dedupe).
    PRIMARY KEY (icao24, time)
);
-- A hypertable is a table split into time chunks, so dropping old data is cheap.
SELECT create_hypertable('positions', 'time', chunk_time_interval => INTERVAL '6 hours');
-- The 48 h retention policy is NOT set here: replayed historical days have old
-- timestamps and would be deleted. Live pollers enable it (db.positions).

CREATE TABLE aircraft (
    icao24       text PRIMARY KEY,
    registration text,
    type_code    text,
    category     text,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- Detector output (filled from M2) ------------------------------------------
CREATE TABLE turb_events (
    id             bigserial PRIMARY KEY,
    icao24         text        NOT NULL,
    time           timestamptz NOT NULL,
    geom           geometry(Point, 4326) NOT NULL,
    alt_ft         integer     NOT NULL,
    h3_cell        h3index     NOT NULL,
    rmsva_g        real        NOT NULL,
    severity       smallint    NOT NULL CHECK (severity BETWEEN 0 AND 3), -- none..severe
    confidence     real        NOT NULL,
    -- Sampling interval the estimate came from: M0 showed RMSVA values are
    -- only comparable at the same interval.
    sample_dt_s    real,
    method_version text        NOT NULL,
    UNIQUE (icao24, time, method_version)
);
CREATE INDEX turb_events_time_idx ON turb_events (time DESC);
CREATE INDEX turb_events_cell_idx ON turb_events (h3_cell, time DESC);
CREATE INDEX turb_events_geom_idx ON turb_events USING gist (geom);

-- Ground truth and weather (filled in M5/M6) --------------------------------
CREATE TABLE pireps (
    id             text PRIMARY KEY,
    obs_time       timestamptz NOT NULL,
    geom           geometry(Point, 4326) NOT NULL,
    alt_ft         integer,
    turb_intensity text,
    turb_type      text,
    raw            text NOT NULL
);
CREATE INDEX pireps_time_idx ON pireps (obs_time DESC);
CREATE INDEX pireps_geom_idx ON pireps USING gist (geom);

CREATE TABLE metars (
    station    text        NOT NULL,
    obs_time   timestamptz NOT NULL,
    raw        text        NOT NULL,
    wind_dir   smallint,
    wind_kt    smallint,
    gust_kt    smallint,
    vis_sm     real,
    ceiling_ft integer,
    flight_cat text,
    PRIMARY KEY (station, obs_time)
);

CREATE TABLE tafs (
    station    text        NOT NULL,
    issue_time timestamptz NOT NULL,
    valid_from timestamptz,
    valid_to   timestamptz,
    raw        text        NOT NULL,
    PRIMARY KEY (station, issue_time)
);

CREATE TABLE sigmets (
    id         text PRIMARY KEY,
    valid_from timestamptz NOT NULL,
    valid_to   timestamptz NOT NULL,
    hazard     text,
    geom       geometry(Polygon, 4326),
    alt_lo_ft  integer,
    alt_hi_ft  integer,
    raw        text
);
CREATE INDEX sigmets_valid_idx ON sigmets (valid_to DESC);
CREATE INDEX sigmets_geom_idx ON sigmets USING gist (geom);

-- Reference data (OurAirports) ----------------------------------------------
CREATE TABLE airports (
    icao         text PRIMARY KEY,
    name         text NOT NULL,
    geom         geometry(Point, 4326) NOT NULL,
    elevation_ft integer,
    country      text
);
CREATE INDEX airports_geom_idx ON airports USING gist (geom);

CREATE TABLE runways (
    id             bigint PRIMARY KEY,
    airport_icao   text NOT NULL REFERENCES airports (icao) ON DELETE CASCADE,
    le_ident       text,
    he_ident       text,
    le_heading_deg real,
    he_heading_deg real,
    length_ft      integer
);
CREATE INDEX runways_airport_idx ON runways (airport_icao);

-- Accuracy page history -----------------------------------------------------
CREATE TABLE validation_runs (
    run_date       date NOT NULL,
    region         text NOT NULL,
    method_version text NOT NULL,
    precision      real,
    recall         real,
    n_pireps       integer NOT NULL,
    n_events       integer NOT NULL,
    PRIMARY KEY (run_date, region, method_version)
);
