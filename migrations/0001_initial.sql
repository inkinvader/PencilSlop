-- PENCILSLOP schema, migration 1: catalog and rounds (Phase 2 MVP).
-- Apply with: npx wrangler d1 migrations apply pencilslop --local | --remote
-- IF NOT EXISTS lets this run safely on the live database, which was created
-- from the same definitions before migrations were introduced.

-- Catalog: one row per artwork. Single source of truth for whether an image is AI
-- and for its crowd vote counts.
CREATE TABLE IF NOT EXISTS images (
  id          TEXT PRIMARY KEY,          -- random hex; image stored as <id>.jpg
  is_ai       INTEGER NOT NULL,          -- 1 = AI-generated, 0 = human-made
  title       TEXT NOT NULL,
  artist      TEXT NOT NULL,
  source_url  TEXT NOT NULL,             -- page credited and linked in the reveal
  license     TEXT NOT NULL,
  ai_evidence TEXT,                      -- AI only: how its AI origin is confirmed
  tier        INTEGER NOT NULL DEFAULT 1,-- difficulty tier (used from Phase 3)
  votes_ai    INTEGER NOT NULL DEFAULT 0,-- players who judged it AI
  votes_real  INTEGER NOT NULL DEFAULT 0 -- players who judged it human
);

-- One row per served pair. Lets the server check answers without ever sending
-- the truth to the browser, and stops a round from being answered twice.
CREATE TABLE IF NOT EXISTS rounds (
  id          TEXT PRIMARY KEY,
  left_id     TEXT NOT NULL REFERENCES images(id),
  right_id    TEXT NOT NULL REFERENCES images(id),
  created_at  INTEGER NOT NULL,          -- ms since epoch, used to enforce the timer
  choice      TEXT,                      -- left | right | both | neither | timeout; NULL until answered
  answered_at INTEGER
);
