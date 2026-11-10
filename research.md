# Research

> EDITING DIRECTIVE: DEVELOPER AND AGENT EDIT THIS FILE COLLABORATIVELY. THE DEVELOPER MUST REVIEW AND APPROVE ITS CONTENT.

Purpose: Keep the evidence, decisions, and current scope that guide the build.

## Working agreement

Developer: Direct the inquiry toward what the next decision needs, judge sources and options, make the consequential decisions, and keep this file current.

Agent: Read the project documents and inspect the project. Help investigate, compare options, and test assumptions quickly. Verify sources directly.

When you begin this step, start a fresh chat so its transcript stays clean. For example:

`Read the project documents and help me research what I need for the next build decision.`

## Inquiry and decisions

- **Cloudflare free tier** (Cloudflare docs, checked 2026-11-09):
  - Workers: 100,000 requests per day and 10 ms CPU time per request.
  - D1: 5 million rows read and 100,000 rows written per day, 5 GB storage.
  - R2: 10 GB storage and free egress.
  - Each answer writes about 3 rows, so class-scale use is far inside the limits.
  - Not verified: whether R2 needs a payment method. This is one reason for skipping R2 in the MVP.
- **Real art source**: the Art Institute of Chicago API. Public-domain artworks have CC0 images. The query excludes "boosted" highlights, but the results still include well-known works (Monet), which suits the easy tier.
- **AI art source**: Wikimedia Commons AI categories, freely licensed files only (public domain, CC0, CC BY, CC BY-SA). The categories are where the script looks, not proof.
- **AI confirmation rule** (revised 2026-11-09 after the Developer couldn't confirm one image): the file page itself must name a generator (Midjourney, DALL·E, Stable Diffusion, ChatGPT, Bing Image Creator, and others) or carry the uploader's `{{PD-algorithm}}` license template, which declares the work machine-generated. The exact evidence is stored per image.
  - The first rule accepted category membership alone. An audit found 4 of 10 AI images, all from one uploader, with no AI statement on the page. Anyone can edit categories, so membership isn't an explicit statement.
  - `ingest.py --recheck-ai` (then `build_catalog.py`) applies the rule to existing entries; it rejected the same 4.
  - "AI-generated images including prompts" (about 2,400 files) and the generator categories (Midjourney, Stable Diffusion, ChatGPT, Bing, DALL·E, Gemini, Flux, Ideogram) were added as search categories. They contain many non-artworks.
- **Finding (evidence)**: in the first build every AI image was square and no human image was. All images are now square-cropped.
- **Finding (evidence)**: some Commons images carry a visible DALL·E watermark strip, or aren't artwork at all (an infographic, a landscape photograph). A manual review of 300 images rejected 46: photorealistic, text or diagrams, duplicates, watermark, frames, two real-person deepfakes, and one sexualized image.
- **Automated growth** (Developer, 2026-11-09): the final build has no set catalog size, so manual review can't scale. It was replaced by fixed rules plus player reports.
- **Budget constraint** (Developer, 2026-11-09): no money for paid services or a card on file. Everything must stay on free tiers with no payment method.
  - Claude API screening (Haiku 5.5, about $0.0001 per image) was built and then removed: there is no free tier.
  - R2 was dropped: it may require a card (not verified).
  - The Workers free plan's 10 ms CPU limit rules out image processing in a Cloudflare cron, so the nightly job runs on GitHub Actions.
- **Free image checks adopted** (Developer, 2026-11-09). The rules-only test ingest had let a text image and a near-duplicate through. Calibration (evidence):
  - Duplicate check (64-bit dHash): the known near-duplicate scored 1 bit from its original; the closest genuinely different catalog pair scored 10. Threshold set at 6.
  - Text check (Tesseract 5.4; sparse-text mode, 2× upscale, word confidence 85 or more, 3 or more alphabetic words): it flagged 8 of the 10 earlier rejects that contain text and none of the 49 other rejects. It produced **0 false positives on 256 catalog paintings**. It missed a stylized book-cover title and a handwritten sign, which are left to player reports.
  - The settings didn't change between confidence 80 and 85, between 3 and 4 words, or between psm 11 and 12.
  - The live re-check hid none of the 255 active images and stored all their hashes.

## Scope

### Stretch goals

As in the approved pitch, unchanged.

### Scope changes

None.

## Sources and credits

- Wrangler (Cloudflare CLI), MIT/Apache-2.0: https://github.com/cloudflare/workers-sdk
- Pillow (image processing in the ingest script), MIT-CMU license: https://python-pillow.org
- Tesseract OCR (text check), Apache-2.0: https://github.com/tesseract-ocr/tesseract; pytesseract wrapper, Apache-2.0: https://github.com/madmaze/pytesseract
- Art Institute of Chicago API and CC0 images: https://api.artic.edu/docs/. Each work's credit and link are stored in the D1 `images` table and shown in the game.
- Wikimedia Commons AI-generated images, under each file's license (public domain, CC0, CC BY, or CC BY-SA). Each work's credit, license, and link are stored in the D1 `images` table and shown in the game.
