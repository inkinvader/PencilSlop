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
- **AI art source**: Wikimedia Commons AI categories. Category membership counts as the explicit AI confirmation. Only freely licensed files are used (public domain, CC0, CC BY, CC BY-SA). "AI-generated paintings" holds only 7 files, so the landscapes, portraits, and "in the style of specific artists" categories were added (about 97 files).
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
