-- PENCILSLOP migration 4: one vote per device per image.
-- The browser remembers which images it has already voted on and asks the server
-- not to count a repeat. Each round records whether each side's vote was counted,
-- so tools that undo votes (ingest.py --void-rounds) subtract only counted votes.
-- Existing rounds all counted their votes, hence the default of 1.
ALTER TABLE rounds ADD COLUMN left_counted INTEGER NOT NULL DEFAULT 1;
ALTER TABLE rounds ADD COLUMN right_counted INTEGER NOT NULL DEFAULT 1;
