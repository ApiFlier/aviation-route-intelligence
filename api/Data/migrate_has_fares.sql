-- Migration: add has_fares flag to airports table
-- Run once against the live database, then DataProcessor will maintain it going forward.

ALTER TABLE airports ADD COLUMN has_fares BOOLEAN DEFAULT FALSE;
ALTER TABLE airports ADD INDEX idx_has_fares (has_fares);

-- Populate from existing route_fares data
UPDATE airports a SET has_fares = FALSE;
UPDATE airports a SET has_fares = TRUE
WHERE EXISTS (
    SELECT 1 FROM routes r
    JOIN route_fares rf ON rf.route_id = r.id
    WHERE r.origin = a.iata
);
