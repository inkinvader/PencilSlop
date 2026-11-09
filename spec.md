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
| Crowd stats | Each answer adds one vote (AI or human) per image. The reveal shows counts from before this vote and hides percentages until an image has at least 3 votes. |
| Streak and best | Streak resets on a new run; best is stored in `localStorage` and the game still works if storage is blocked. |
| Shared across devices | Two devices playing the deployed URL see each other's votes in the stats. |
| Reports | After a round is answered, each image has a Report button. One report counts per image per round; reporting before answering is refused (409). At 3 reports an image stops being served. |
| Catalog growth | A nightly job adds new images with no manual review, using only the fixed rules below. There is no set catalog size. |
| Fixed catalog rules | Human: AIC public-domain painting. AI: the Commons file page names a generator or carries `{{PD-algorithm}}`. Both: free license, JPEG/PNG/WebP, no more than 1.6:1 before the square crop, no "not artwork" title words. Every refused source is recorded and never fetched again. |
| Cost | The whole system runs on free tiers with no payment method: Cloudflare Workers + D1 and GitHub Actions. |

Provisional, not yet specified: difficulty tiers, avoiding repeat images within a run, branding polish, accessibility pass, phone layout refinements.

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
  scripts/ingest.py: fetch candidates -> fixed rules -> normalize to public/img/
                     -> git commit + push -> wrangler deploy -> insert D1 rows
```

### Data and state

- **D1 `images`**: the catalog. It is the source of truth for whether an image is AI, its credit and license, its evidence, whether it is active, its report count, and its vote counts.
- **D1 `rejected`**: every source refused by a rule or a reject command. The ingest never fetches these again.
- **D1 `rounds`**: every served pair, its timestamp, and its choice once answered. This lets the server check answers and reject repeats.
- **D1 `reports`**: one row per (round, image) report. `images.reports` is the running count.
- **`public/img/`**: the image files, committed to git and deployed as static assets. A file without an `images` row is never served in a round.
- **Browser**: only the current streak, the timer, and the personal best (`localStorage`).
- `db/catalog.json`, `db/seed.sql`, `db/rejected.json`: Phase 2 records, imported into D1 on 2026-11-09 and no longer read.

### Interaction loop

Play → `POST /api/round` (active images only) → load both images → timer starts → player chooses (or the timer ends) → `POST /api/answer` → the server checks, records votes, and returns the reveal → captions show the truth, credit, crowd percentages, and a Report button → Next (if correct) or Play again.

### Visual and content rules

- Kahoot-inspired quiz-show look with the art first. Layout is a column: top bar (wordmark, streak, best, timer), then the artwork pair filling the remaining height, then a compact answer strip.
- Colors are tokens in `public/style.css` (`--purple`, `--ink`, `--pencil`, `--metal`, and the four answer colors).
- Left and right answers sit under their image (2×2 grid on phones).
- Reveal borders: green for human, red for AI.
- Every image goes through the same normalization in `ingest.py`: a 640×640 centre crop, JPEG quality 82, no metadata.

### Structure rules

- API logic only in `src/worker.js`. Presentation only in `public/`. Catalog changes only through `scripts/ingest.py`, never hand-written SQL.
- Schema changes are new files in `migrations/`, applied with `wrangler d1 migrations apply`.
- Publish order is images live first, then rows. Always `git pull` before deploying by hand, because the nightly job commits images.
- Images are hidden (`active = 0`), never deleted, so past rounds stay valid.
- Tunable constants (timer, grace period, AI chance range, minimum votes, reports to hide, crop size) are named at the top of the file that uses them.

### Decisions and changes

- **Single Worker with static assets** (2026-11-09). One deploy, no separate host.
- **Square 640 px crop for all images** (2026-11-09). The first catalog showed every AI image was square and no human one was, which gave the answer away. Trade-off: the edges of non-square works are trimmed.
- **Vote counters on `images`** rather than computing stats from `rounds` each time. Cheap reads; `rounds` still keeps each choice.
- **AI proof must be on the file page** (2026-11-09). Category membership is user-editable and let 4 unconfirmed images in.
- **Automated growth with no manual review** (2026-11-09, Developer). The final catalog has no set size, so review by hand can't scale. D1 replaced `catalog.json` as the source of truth, and player reports were added as the backstop.
- **Free design: no R2, no paid screening** (2026-11-09, Developer has no budget). R2 (images outside the deploy) and Claude vision screening were built and tested locally, then dropped: R2 may need a card and the Claude API is paid. Images stay in the repo as static assets (the free plan allows 20,000 files), and screening is fixed rules plus reports. Trade-off: photorealistic images, text inside images, watermarks without text, and near-duplicates can get in until players report them.
- **Nightly job on GitHub Actions, not a Cloudflare cron** (2026-11-09). The Workers free plan allows 10 ms CPU per run, too little to process images.
