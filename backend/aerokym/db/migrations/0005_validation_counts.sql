-- M6: the accuracy page.
--
-- A day rarely has enough matched pilot reports to trust on its own, so the
-- page pools several days. Pooling needs the raw counts (you cannot average
-- precisions), so they are stored next to the per-day precision and recall.

ALTER TABLE validation_runs
    ADD COLUMN n_matched       integer NOT NULL DEFAULT 0,  -- reports with a detector window nearby
    ADD COLUMN n_smooth        integer NOT NULL DEFAULT 0,  -- of those, reports of smooth air
    ADD COLUMN n_light         integer NOT NULL DEFAULT 0,
    ADD COLUMN n_moderate_plus integer NOT NULL DEFAULT 0,
    ADD COLUMN tp              integer NOT NULL DEFAULT 0,  -- said moderate+, pilot said moderate+
    ADD COLUMN fp              integer NOT NULL DEFAULT 0,  -- said moderate+, pilot said less
    ADD COLUMN fn              integer NOT NULL DEFAULT 0,  -- said less, pilot said moderate+
    ADD COLUMN auc             real,                        -- null when too few reports
    ADD COLUMN n_aircraft      integer NOT NULL DEFAULT 0,
    ADD COLUMN match_config    text,
    ADD COLUMN computed_at     timestamptz NOT NULL DEFAULT now();
