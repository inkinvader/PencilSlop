# Plan

> EDITING DIRECTIVE: DEVELOPER AND AGENT EDIT THIS FILE COLLABORATIVELY. THE DEVELOPER MUST REVIEW AND APPROVE ITS CONTENT.

Purpose: Track the five-week map, the next build checkpoints, testing, and simplification reviews.

## Working agreement

Developer: Choose priorities, plan only the next few checkpoints in detail, test results directly, and keep the map current.

Agent: Read the project documents and inspect the project. Where practical, keep each checkpoint small enough to run and judge. Do not expand scope or mark the Developer's verification complete.

When you begin this step, start a fresh chat so its transcript stays clean. For example:

`Read the project documents, inspect the project, and help me complete the next checkpoint.`

## Five-week map

As in the approved pitch. Phase 2 (Nov 2–9): MVP deployed to a public Cloudflare URL.

## Checkpoints

Phase 2:

1. [x] Install tooling: Node.js 26.13 LTS and Wrangler 4.159.
2. [x] D1 schema and seed catalog: 10 human (Art Institute of Chicago, CC0) and 10 AI (Wikimedia Commons), normalized to 640×640.
3. [x] API: `/api/round` and `/api/answer`, tested with curl locally.
4. [x] Game page: timer, streak, best, and reveal, tested in headless Edge on desktop and phone sizes.
5. [x] Seed image review. The Developer delegated the choice; the Agent rejected 6 images, listed with reasons in `db/rejected.json`: 2 with DALL·E watermarks, 3 non-artworks (infographics, a photograph), and 1 framed photo that hints human. Replacements were fetched and checked by eye.
6. [x] Deploy: Cloudflare login, create the remote D1 database, load the schema and seed, `wrangler deploy`. Live at https://pencilslop.pencilslop.workers.dev
7. [ ] Two-device test on the public URL, plus a network check in DevTools.

## Testing and evidence

2026-11-09, local (`wrangler dev`, local D1):
- `/api/round` returned only `roundId`, two `/img/<hex>.jpg` paths, and `seconds`. The page and images were served (200).
- `/api/answer`: the first answer returned the truth and credits. A repeat gave 409, an invalid choice 400, and an unknown round 404.
- An answer sent 14 s after the round started was recorded as `timeout` and counted as wrong.
- Over 200 rounds the answers were Neither 70, Left 52, Right 41, Both 37. The design expects about 37% / 23% / 23% / 17%.
- Vote counts built up across rounds and appeared in later reveals.
- Headless Edge at 1280×800 and 390×844: start, round, and reveal screens rendered. A bug showed the Next button during the round, because CSS `display` overrode `hidden`. Fixed with a global `[hidden]` rule and re-checked.
- The first catalog build revealed AI images by their square shape. Fixed by square-cropping all images; all 20 are now 640×640.

2026-11-09, deployed:
- The first requests failed with a TLS handshake error while the new `workers.dev` certificate was being issued. The site was up within minutes.
- Live API: the round response held only an ID and image paths, images were served (200 image/jpeg), the answer returned the truth, and a repeat answer gave 409.
- Headless Edge against the live URL (desktop and phone): the full round and reveal worked, and "Not enough votes yet" showed on the fresh database.
- Agent test rounds cast about 5 votes in the live database.

## Simplification reviews

None yet (first review at the end of Phase 3).
