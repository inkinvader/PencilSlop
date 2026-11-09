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

Provisional, not yet specified: difficulty tiers, avoiding repeat images within a run, branding polish, accessibility pass, phone layout refinements.

## Architecture

### Overview

One Cloudflare Worker serves everything:

```
Browser (public/: index.html, game.js, style.css, img/*.jpg)
   |  static files served directly by Cloudflare's asset handler
   |  POST /api/round, POST /api/answer
   v
Worker (src/worker.js) --> D1 database "pencilslop" (images, rounds)

Offline: scripts/build_catalog.py --> public/img/*.jpg + db/catalog.json --> db/seed.sql
```

### Data and state

- **`db/catalog.json`**: the reviewable list of artworks (ID, AI or not, title, artist, source, license, AI evidence). It is the source of truth for catalog content; `db/seed.sql` is generated from it.
- **D1 `images`**: the catalog as loaded on the server, plus vote counts. This is the source of truth for whether an image is AI and for crowd stats.
- **D1 `rounds`**: every served pair, its timestamp, and its choice once answered. This lets the server check answers and reject repeats.
- **Browser**: only the current streak, the timer, and the personal best (`localStorage`). The browser never stores answers.

### Interaction loop

Play → `POST /api/round` → load both images → timer starts → player chooses (or timer ends) → `POST /api/answer` → server checks, records votes, returns the reveal → captions and borders show the truth, credits, and crowd percentages → Next (if correct) or Play again (if the run is over).

### Visual and content rules

- Kahoot-inspired quiz-show look with the art first. Layout is a column: top bar (wordmark, streak, best, timer), then the artwork pair filling the remaining height, then a compact answer strip.
- Colors are tokens in `public/style.css` (`--purple`, `--ink`, `--pencil`, `--metal`, and the four answer colors).
- Left and right answers sit under their image (2×2 grid on phones).
- Reveal borders: green for human, red for AI.
- Every image goes through the same normalization in `build_catalog.py`. Images more than 1.6:1 from square are skipped rather than cropped hard.

### Structure rules

- API logic only in `src/worker.js`. Presentation only in `public/`. Catalog building only in `scripts/`.
- Catalog changes go through `catalog.json` and the build script, never hand-edited SQL.
- Tunable constants (timer, grace period, AI chance range, minimum votes) are named at the top of the file that uses them.

### Decisions and changes

- **Single Worker with static assets instead of Pages + Workers + R2** (2026-11-09). One deploy, no R2 setup. Adding images means redeploying, which suits a curated catalog. Can move images to R2 if the catalog grows large.
- **Square 640 px crop for all images** (2026-11-09). The first catalog showed every AI image was square and no human one was, which gave the answer away. Trade-off: the edges of non-square works are trimmed.
- **Vote counters on `images`** rather than computing stats from `rounds` each time. Cheap reads; `rounds` still keeps each choice.
