-- PENCILSLOP schema, migration 2: automated catalog pipeline.
-- D1 becomes the source of truth for the catalog (replacing db/catalog.json),
-- images can be hidden without deleting them, and players can report images.

-- Only active images are served. Images are hidden (not deleted) so past rounds
-- that reference them stay valid.
ALTER TABLE images ADD COLUMN active INTEGER NOT NULL DEFAULT 1;
ALTER TABLE images ADD COLUMN reports INTEGER NOT NULL DEFAULT 0;
ALTER TABLE images ADD COLUMN added_at INTEGER;   -- ms since epoch; NULL for the seed catalog
ALTER TABLE images ADD COLUMN screening TEXT;     -- how the image passed screening

-- Sources that were reviewed and refused. The pipeline never fetches these again.
CREATE TABLE IF NOT EXISTS rejected (
  source_url  TEXT PRIMARY KEY,
  title       TEXT,
  reason      TEXT NOT NULL,
  rejected_at INTEGER
);

-- One report per image per round, so a single player can't stack reports by
-- clicking repeatedly. images.reports is the running count.
CREATE TABLE IF NOT EXISTS reports (
  round_id    TEXT NOT NULL REFERENCES rounds(id),
  image_id    TEXT NOT NULL REFERENCES images(id),
  created_at  INTEGER NOT NULL,
  PRIMARY KEY (round_id, image_id)
);
