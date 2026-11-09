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
  - `build_catalog.py --recheck-ai` applies the rule to existing entries; it rejected the same 4.
  - "AI-generated images including prompts" (about 2,400 files) was added as a search category. It contains many non-artworks, so the manual visual review stays necessary.
- **Finding (evidence)**: in the first build every AI image was square and no human image was. All images are now square-cropped.
- **Finding (evidence)**: some Commons images carry a visible DALL·E watermark strip, or aren't artwork at all (an infographic, a landscape photograph). They need manual review.

## Scope

### Stretch goals

As in the approved pitch, unchanged.

### Scope changes

None.

## Sources and credits

- Wrangler (Cloudflare CLI), MIT/Apache-2.0: https://github.com/cloudflare/workers-sdk
- Pillow (image processing in the build script), MIT-CMU license: https://python-pillow.org
- Art Institute of Chicago API and CC0 images: https://api.artic.edu/docs/. Each work's credit and link are in `db/catalog.json` and shown in the game.
- Wikimedia Commons AI-generated images, under each file's license (public domain, CC0, CC BY, or CC BY-SA). Each work's credit, license, and link are in `db/catalog.json` and shown in the game.
