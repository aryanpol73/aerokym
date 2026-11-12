-- M2 replaced RMSVA with a roughness index (docs/method.md), so the events
-- table is renamed to say what it really stores.
--
-- One row = one 3-minute level-cruise window of one aircraft, INCLUDING smooth
-- ones (severity 0). Smooth windows matter: a map cell is only believable when
-- we know how many aircraft flew through it and felt nothing.

ALTER TABLE turb_events RENAME COLUMN rmsva_g TO roughness_index;
ALTER TABLE turb_events ADD COLUMN n_samples smallint;

-- h3_cell is stored at resolution 5 (hexes about 9 km across). Coarser map
-- cells are derived with h3_cell_to_parent, so the map resolution can change
-- without re-running the detector.
COMMENT ON COLUMN turb_events.h3_cell IS 'H3 resolution 5 cell of the window midpoint';

-- The spec planned a TimescaleDB continuous aggregate (turb_cells_5m). Not
-- created: events are small (one row per aircraft per minute), and a plain
-- GROUP BY over the last hour is fast with this index. Revisit if it is not.
CREATE INDEX turb_events_method_time_idx ON turb_events (method_version, time DESC);
