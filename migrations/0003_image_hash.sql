-- PENCILSLOP schema, migration 3: perceptual hash for near-duplicate detection.
-- 16 hex digits (64-bit dHash of the normalized image), filled by
-- scripts/ingest.py for new images and by --recheck-images for existing ones.
ALTER TABLE images ADD COLUMN phash TEXT;
