# Specification

> EDITING DIRECTIVE: DEVELOPER AND AGENT EDIT THIS FILE COLLABORATIVELY. THE DEVELOPER MUST REVIEW AND APPROVE ITS CONTENT.

Purpose: Keep a concise, testable description of what the project should do now and how the system is structured to do it.

## Working agreement

Developer: Approve the intended behavior and the system's structure, test directly, understand each part well enough to explain it in your own words, and update this file whenever either changes.

Agent: Read the project documents and inspect the project. Turn the Developer's decisions into acceptance checks without inventing product choices or freezing details that still need testing. Propose structures suited to the project's scope, with their trade-offs, in plain language. Keep the architecture consistent with the actual project and point out where they have drifted apart. Do not restructure the project without the Developer's approval.

When you begin this step, start a fresh chat so its transcript stays clean. For example:

`Read the project documents, inspect the project, and help me specify what the next version should do.`

## Intended experience

Phase 2 MVP (approved 2026-11-09). A player opens the public URL, presses Play, and sees two artworks. Within 10 seconds they choose **Left is AI**, **Right is AI**, **Both**, or **Neither**. A correct answer adds to the streak; a wrong answer or timeout ends the run. After every answer the game reveals which work is AI, credits and links each work's source, and shows what percentage of earlier players judged each work as AI. The personal best is kept on the device.

## Behavior and acceptance checks

Approved for Phase 2:

| Behavior | Counts as working |
|---|---|
| Pair selection | Each round picks an AI chance uniformly in 20–60%; each slot is independently AI with that chance. Over many rounds all four answers occur (about Neither 37%, Left 23%, Right 23%, Both 17%). |
| No answer leak | The round response holds only a round ID and two neutral image URLs. All images are 640×640 JPEGs with no metadata, so shape and file properties don't reveal the answer. |
| Server-checked answer | The server compares the choice with D1 and returns the truth. A second answer to the same round is rejected (409). Unknown rounds (404) and invalid choices (400) are rejected. |
| Timer | 10 s on the client, starting once both images have loaded. The server records any answer later than 13 s after the round was served as a timeout. A timeout ends the run and casts no votes. |
| Crowd stats | Each answer adds one vote (AI or human) per image. The reveal shows "X% of N players got it right" (for AI: share who said AI; for human: share who said human), counted from before this vote, and hides it until an image has at least 3 votes. |
| Streak and best | Streak resets on a new run; best is stored in `localStorage` and the game still works if storage is blocked. |
| Shared across devices | Two devices playing the deployed URL see each other's votes in the stats. |
| Reports | After a round is answered, each image has a Report button. One report counts per image per round; reporting before answering is refused (409). At 3 reports an image stops being served. |
| Catalog growth | A nightly job adds new images with no manual review, using only the fixed rules below. There is no set catalog size. |
| Fixed catalog rules | Human: AIC public-domain painting. AI: the Commons file page names a generator or carries `{{PD-algorithm}}`. Both: free license, JPEG/PNG/WebP, no more than 1.6:1 before the square crop, no "not artwork" title words. Every refused source is recorded and never fetched again. |
| Image checks | After normalization: refused if its dHash is within 6 bits of any stored image (near-duplicate), or if Tesseract reads 3 or more confident words (text). `--recheck-images` applies the same checks to the active catalog. |
| Logo | The logo is on the start screen, at the top of the clock post on desktop (75 px), alone in the header on phones (66 px), and as a square icon in the browser tab. |
| Cost | The whole system runs on free tiers with no payment method: Cloudflare Workers + D1 and GitHub Actions. |

Approved for Phase 3 (2026-11-16):

| Behavior | Counts as working |
|---|---|
| Difficulty tiers | Every image has tier 1 (easy), 2 (medium) or 3 (hard), set by fixed rules in `ingest.py`. Human: AIC `is_boosted` (famous) = easy, `has_not_been_viewed_much` = hard, other = medium. AI: Commons upload year 2022 or earlier = easy, 2023 = medium, 2024 or later = hard. `--retier` applies the rules to existing images. |
| Difficulty ramp | The page sends its streak with `/api/round`. Each slot picks a tier with odds by streak (easy/medium/hard): 0 → 50/30/20, 5 → 30/45/25, 10 → 15/40/45, 15+ → 10/25/65, linear in between. If the tier has no image of the needed kind, any tier is used. A local run of 1,000 rounds per streak matches the odds within a few percent. |
| Tier growth | The nightly human picks rotate through the three AIC groups, so each tier grows. |

Approved for Phase 3, museum redesign (2026-11-16):

| Behavior | Counts as working |
|---|---|
| Gallery look | Quartz wall, a gold frame per piece, red velvet curtains, wall labels, metal answer plaques (brass, bronze, copper, pewter, each a different finish). All drawn in CSS/SVG, no texture images. Logo recolored to the same palette. |
| Curtain sequence | Start: curtains closed behind the start card. Play: the pair loads, curtains slide open, then the clock starts. After the answer the curtains stay open for the reveal. Next / Play again: curtains slide closed, labels clear, the next pair loads behind them, curtains open. The curtains always slide (Developer: slide, not fade, also with reduced motion). No artwork is visible before its curtain opens. |
| Clock | Between the pieces: an analog face whose hand sweeps once over the 10 s, with the countdown number in the centre (red for the last 3 s). Desktop: streak above the clock, best below. Phone: streak left, best right. |
| Layout | Desktop: frame, clock post, frame in one row; answers at the bottom; the round fits a 1280×800 window without scrolling. Phone (≤ 700 px): frame, clock strip, frame, stacked; answers 2×2, pinned to the bottom; the round fits 390×844, and the reveal may scroll. No horizontal scrolling. |
| No repeats in a run | The page sends the IDs already shown this run (`seen`); the Worker skips them. Repeats are allowed again only if a run has seen every image of a kind. |
| Unservable images | An image that can't be shown is hidden (`active = 0`). In play: if an image fails to load, the page calls `/api/missing`, and the Worker hides the image only if the deployed file is really missing (checked through the `ASSETS` binding, so a player can't hide a working image). The page then loads a different pair, up to 3 tries, before opening the curtains. Nightly: every ingest first checks that each active image is served (`--remote`: over HTTP from the live site; `--local`: the file exists) and hides the rest. `--check-files` runs that check alone. |
| Reveal credit | Human works: title, artist, and a Source link. AI images: no title, artist, or link (Developer, 2026-11-17), except that an AI image whose license requires attribution (CC BY, CC BY-SA) shows a small License link. The Worker sends only what is shown. |
| Developer's own AI images | `ingest.py --add-own DIR --tool NAME` adds the developer's own generated images (pitch: "a small number of images I generate myself"). They pass the same normalization and image checks and are tier 3 (hard). The generator is recorded in the AI evidence (ChatGPT so far; `--set-own-tool` changes it); like other AI images they show no credit. Originals stay in `scripts/raw/` (not in git). |
| Speed bonus | A correct answer within 0.5 s of the clock starting adds +2 bonus points, within 1 s +1. A `+N` badge to the right of the streak number appears with the first bonus of a run (hidden before) and shows the run's total bonus. Points total = streak + bonus, shown in the end-of-run verdict. Best is the best points total (`localStorage`). "New best!" shows once per run, on the answer that first passes the record held at the start of the run, and not when there was no record. Timing is measured in the browser; only this device's best depends on it. Difficulty still follows the streak. |
| Accessibility | Keys L / ← left, R / → right, B both, N neither; visible focus ring; focus moves to Next after the reveal; round start and verdict announced to screen readers; text contrast at least 4.5:1; buttons at least 44 px tall; image alt text neutral until the reveal, then title and artist. |

Provisional, not yet specified: data-driven difficulty (Phase 4).

## Architecture

### Overview

One Cloudflare Worker serves the game; a nightly GitHub Actions job grows the catalog.

```
Browser (public/: index.html, game.js, style.css, img/<id>.jpg)
   |  static files served directly by Cloudflare's asset handler
   |  POST /api/round, /api/answer, /api/report
   v
Worker (src/worker.js) --> D1 database "pencilslop" (images, rounds, reports, rejected)

Nightly (GitHub Actions, .github/workflows/nightly-ingest.yml):
  scripts/ingest.py: fetch candidates -> fixed rules -> normalize -> image checks
                     (duplicate hash, OCR text) -> save to public/img/
                     -> git commit + push -> wrangler deploy -> insert D1 rows
```

### Data and state

- **D1 `images`**: the catalog. It is the source of truth for whether an image is AI, its credit and license, its evidence, whether it is active, its report count, and its vote counts.
- **D1 `rejected`**: every source refused by a rule or a reject command. The ingest never fetches these again.
- **D1 `rounds`**: every served pair, its timestamp, and its choice once answered. This lets the server check answers and reject repeats.
- **D1 `reports`**: one row per (round, image) report. `images.reports` is the running count.
- **`public/img/`**: the image files, committed to git and deployed as static assets. A file without an `images` row is never served in a round.
- **Browser**: only the current streak, the image IDs seen this run, the timer, and the personal best (`localStorage`).

### Interaction loop

Play → curtains closed → `POST /api/round` with streak and seen IDs (active images only) → load both images → curtains open → timer starts → player chooses (or the timer ends) → `POST /api/answer` → the server checks, records votes, and returns the reveal → captions show the truth, credit, crowd percentages, and a Report button → Next (if correct) or Play again → curtains close.

### Visual and content rules

- Museum gallery look with the art first (2026-11-16, replaces the Phase 2 quiz-show look). Layout is a column: top line (phone: the logo; desktop: a margin only), then the exhibit (frame, clock post, frame), then the console (answer plaques during a round, result after it). The layout model is described at the top of `public/style.css`; every artwork is a square of side `--art`.
- Colors and sizes are tokens in `public/style.css` (`--wall`, `--ink`, `--gold`, `--velvet`, `--ai`, `--human`, ...).
- Left and right answers sit at the outer ends of the plaque row (top row of the 2×2 grid on phones). On phones, where the pieces stack, every side name reads Top / Bottom instead of Left / Right: plaques, verdict, and image descriptions (`sideName` in `game.js`, same 700 px breakpoint as `style.css`).
- Reveal: a red ring and "AI-generated" label for AI, a green ring and "Human-made" for human. The label text means color is never the only cue.
- Every image goes through the same normalization in `ingest.py`: a 640×640 centre crop, JPEG quality 82, no metadata.
- Logo (Developer's new design, 2026-11-16): `branding/logo.png` is the master (white background, no wordmark; the page shows the name as text beside it). The served files are built from it with the background made transparent, at three sizes of the same design: `public/logo.png` (start screen), `public/logo-mark.png` (header) and `public/favicon.png` (64 px, browser tab). Rebuild all three when the master changes.

### Structure rules

- API logic only in `src/worker.js`. Presentation only in `public/`. Catalog changes only through `scripts/ingest.py`, never hand-written SQL.
- Schema changes are new files in `migrations/`, applied with `wrangler d1 migrations apply`.
- Publish order is images live first, then rows. Always `git pull` before deploying by hand, because the nightly job commits images.
- Images are hidden (`active = 0`), never deleted, so past rounds stay valid.
- Tunable constants (timer, grace period, AI chance range, tier odds, AI tier years, minimum votes, reports to hide, crop size) are named at the top of the file that uses them.

### Decisions and changes

- **Museum redesign** (2026-11-16, Developer). Replaced the Kahoot-style look with a gallery: frames, curtains, a clock between the pieces. The pitch listed a gallery as one of its branding options.
- **Rule-based tiers until votes exist** (2026-11-16, Developer). The pitch planned hand-assigned tiers, but the catalog grows with no manual review, and only 8 images had 3+ votes. Tiers come from source signals instead (museum popularity flags, AI upload year as a proxy for generator quality). Phase 4 is meant to re-rank by how often each image fooled players. Trade-off: upload year is only a rough stand-in for how convincing an AI image is.
- **Single Worker with static assets** (2026-11-09). One deploy, no separate host.
- **Square 640 px crop for all images** (2026-11-09). The first catalog showed every AI image was square and no human one was, which gave the answer away. Trade-off: the edges of non-square works are trimmed.
- **Vote counters on `images`** rather than computing stats from `rounds` each time. Cheap reads; `rounds` still keeps each choice.
- **AI proof must be on the file page** (2026-11-09). Category membership is user-editable and let 4 unconfirmed images in.
- **Automated growth with no manual review** (2026-11-09, Developer). The final catalog has no set size, so review by hand can't scale. D1 replaced `catalog.json` as the source of truth, and player reports were added as the backstop.
- **Free design: no R2, no paid screening** (2026-11-09, Developer has no budget). R2 (images outside the deploy) and Claude vision screening were built and tested locally, then dropped: R2 may need a card and the Claude API is paid. Images stay in the repo as static assets (the free plan allows 20,000 files), and screening is fixed rules plus reports. Trade-off: photorealistic images, text inside images, watermarks without text, and near-duplicates can get in until players report them.
- **Nightly job on GitHub Actions, not a Cloudflare cron** (2026-11-09). The Workers free plan allows 10 ms CPU per run, too little to process images.
