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
7. [x] Phone test on the public URL. The Developer confirmed "it works on phone" (2026-11-09). The network check was done by the Agent in headless Edge: the browser's round response holds no answer. A Developer DevTools check is still open.

Phase 2, automated catalog (approved 2026-11-09):

8. [x] D1 migrations (`active`, `reports`, `added_at`, `screening`; `rejected` and `reports` tables). Applied live; 254 rows and their votes kept.
9. [x] Worker: serve active images only; `/api/report` with auto-hide at 3 reports.
10. [x] `scripts/ingest.py` (fixed rules, publish order) and the nightly workflow.
11. [x] Developer: create a Cloudflare API token and add the two GitHub secrets, then trigger one manual workflow run.
12. [x] Logo on the page: full logo on the start screen, mark-only version in the header and browser tab (checked in headless Edge).
13. [x] Free image checks: near-duplicate (dHash) and text (Tesseract), calibrated, in ingest and `--recheck-images`; migration 0003 applied live. Obsolete `db/` files removed.

Phase 3, difficulty tiers (approved 2026-11-16):

14. [x] Tiers by fixed rules in `ingest.py` (new images and `--retier`), streak-based odds in the Worker, streak sent by the page. Tested locally.
15. [x] Deploy, `--retier` on the live catalog, and check a live round.
16. [ ] Developer: play a run and judge whether the early rounds feel easier.

Phase 3, museum redesign (approved 2026-11-16), with repeat avoidance and an accessibility pass:

17. [x] Gallery page: frames, curtains, clock post, wall labels, metal plaques, recolored logo. Worker skips images already seen in the run. Tested locally in headless Edge.
18. [ ] Deploy and check the live page.
19. [ ] Developer: play on desktop and phone and judge the look and the curtain timing.
20. [x] Speed bonus (+2 within 0.5 s, +1 within 1 s), points total, best = best points. Tested locally.
21. [x] Deploy the speed bonus and the logo changes.
22. [x] Hide images that can't be shown: `/api/missing` in play, plus a served-file check before every ingest (`--check-files`). Tested locally, then deployed.

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

2026-11-09, automated pipeline:
- Local: migrations applied. R2 serving, reports (409 before answering, one per round, 400 on a bad side), auto-hide at the 3rd report, and the report button in Edge all worked. The R2 path was later removed (free design).
- Local rules-only ingest of 2 + 2: the AI pair included a protest sign with text and a near-duplicate of an existing image. Rules alone let both through.
- Live: migrations applied with votes kept (116, then 122 with live play). 58 rejections imported. Round, image, and answer worked, and a report before answering was refused.
- Live end-to-end ingest of 1 + 1: images were committed and pushed (`a00a7f0`), deployed, served (200), and their rows added. The text-sign image was then hidden with `--reject`.

2026-11-16, nightly workflow:
- The scheduled runs from Nov 12 to 16 all failed because the repository had no secrets (wrangler: "set a CLOUDFLARE_API_TOKEN").
- The Developer created a custom token (Account: Workers Scripts Edit, D1 Edit) and added `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`.
- Manual run 43967068822 (2 human + 1 AI) passed in 52 s. It committed `4de44f7`, deployed, and added the rows. The active catalog is now 195 human and 63 AI. All 3 new images were served live (200 image/jpeg).

2026-11-16, difficulty tiers (local):
- `--retier --local` set tiers on 262 of 263 images (one AI file no longer found on Commons; left unchanged). Human: all 195 medium, because the earlier ingest took the museum's top search results, which are all "regular" works. AI: 23 easy, 21 medium, 24 hard.
- 1,000 rounds per streak, tiers of the AI slots served (target in brackets): streak 0: 49/31/20 (50/30/20); 5: 31/43/26 (30/45/25); 10: 14/39/47 (15/40/45); 15: 11/24/65 (10/25/65).
- `/api/round` with no body or a non-number streak is served as streak 0.
- The first 6 human candidates alternate tiers 1, 2, 3, 1, 2, 3.

2026-11-16, difficulty tiers (live):
- Deployed. `--retier --remote` set tiers on 258 of 259 images. A one-time `--human 30` added 19 famous (easy) and 11 rarely viewed (hard) works; 3 downloads returned HTTP 403 and will be retried. Nearly all early medium candidates were already in the catalog, so their turns went to easy and hard. Live human tiers: 19 / 195 / 11; AI: 20 / 20 / 23.
- Live `/api/round` at streak 0 and 15 served pairs.

2026-11-16, museum redesign (local, headless Edge at 1280×800 and 390×844):
- The curtains start opening about 50–100 ms after Play. The drapes fully cover both artworks until then. The clock and answers start 940–970 ms after opening begins, when the drapes are fully clear (offset −231 px desktop, −129 px phone).
- At 2.5 s the timer shows 8 and the hand is at about 90°.
- Key N answered the round. Focus moved to Next. The wall labels showed the verdict, title, artist, votes, source and Report.
- Next closed the curtains, cleared the labels and opened on a new pair.
- Round layout: desktop page width 1280 (artwork 448 px, console bottom 740 of 800); phone width 390 (artwork 250 px, console bottom 817 of 844).
- A first run found a 1344 px page width, caused by the spotlight glow, and visible seams in the wall texture. Both were fixed and re-checked.
- Contrast: the bronze and copper plaques first measured 3.4–3.5:1 at their darkest corner. After darker text and lighter corner colors, every text pair measures 4.7:1 or more.
- Developer feedback: "New best!" now shows only on the round that first passes the record held at the start of the run, and only when there was a record. Artwork bigger and clock smaller. Desktop: art 448 → 460 px, clock 136 → 96 px. Phone: art 250 → 272 px, clock 80 → 60 px. The wall labels were clipping their last line; their box is now sized to the content (118 of 118 px, no line clipped).
- Local-only: 9 local D1 rows from old ingest tests have no image file, which shows a broken image locally. All 288 live active images have their files.
- Speed bonus (headless Edge, answers forced correct by wrapping the page's fetch; stored best 3): the badge was hidden at the start. Answers at about 150 ms, 700 ms and 2 s gave the badge +2, then +3, then +3, and Best 3 → 5 → 6. "New best!" appeared only on the second answer (points 3 → 5, first past 3). The losing round said "Streak 3 + speed bonus 3 = 6 points" and stored 6. A new run hid the badge again. Same results on desktop and phone.
- Logo (Developer request): the eyes now mirror each other about the head's centre (x 210 and 330; the open eye was at 300). The open eye is now an empty socket with the pencil tip stabbed into it, and the cracks and sparks moved with it. Masters and served copies were updated together. The start-screen logo grew from 150 to 230 px (phone about 195 px), and the header mark from 36 to 42 px (phone 30 to 38) within the same header height, so artwork size is unchanged (460 / 272 px).
- Logo, second pass (Developer request): cracks and sparks removed; teeth are now five separate white teeth (colors reversed from the dark bar); the pencil has no tip and comes out of the eye socket, with the socket widened to r 30 to fit the 52 px pencil and shaded fully dark inside so the buried end reads as deep. Checked in headless Edge at 300 px, 200 px and 44 px.
- New logo (Developer's PS design, PNG): it is the master `branding/logo.png`. The served `logo.png` (480 px, 24 KB), `logo-mark.png` (192 px tall, 7 KB) and `favicon.png` (64 px) were built with a transparent background. The page now uses them instead of the SVGs. Checked on the start card and in the header in headless Edge; the layout is unchanged (art 460 / 272 px). The old SVG logo files are no longer used and were kept until the Developer decides.
- Developer hid every reported image and the remaining robot images (2026-11-17), all AI: 16 hidden with `--reject`, so their sources are blocked. That makes 22 hidden after the 6 removed by player reports. Afterwards no active image had reports and `--find-robots` matched 0 of 39. Active catalog: 225 human, 51 AI.
- No robot subjects in new AI images (Developer request): new rule in `ingest.py`, plus a read-only `--find-robots` scan. The scan found 7 of the 56 active Commons AI images; "AIBRAIN" (a mechanical brain) was missed by the words and found by eye. Text such as "machine-generated" or "Midjourney Bot" does not trigger the rule.
- Answer labels now read "Both are AI" / "Neither are AI" (Developer wording), in the plaques and the verdict. They stay on one line at 320, 390 and 1280 px.
- Reports so far (2026-11-17): 25 reports. 2 images hidden at 3 reports, 5 at 2, 9 at 1. All 16 reported images are AI.
- One vote per device per image (local tests): with a repeat flag on the left only, the left image gained 0 votes and the right 1 (flags 0/1 stored); with both flags, 0 and 0; with an old page sending no flags, 1 and 1. The reveal was returned each time. In headless Edge a fresh device sent `countLeft/Right: true` and remembered 2 images; a device that had voted on every image sent `false` for both. Migration 0004 was applied live: 630 past rounds, all marked counted. `--void-rounds` now skips uncounted sides.
- Phone layout (Developer request): the logo (40 px), streak, clock (52 px) and best now share one top row. The answer plaques (44 px, smaller text) sit between the two pieces, and the bottom bar is gone. Artwork grew from 259 to 307 px at 390×844, with everything on screen (bottom piece ends at 828 of 844). The reveal puts the verdict and Next between the pieces. The start card reads "Either one, both, or neither". Desktop is unchanged (500 px). Tested locally in headless Edge.
- Test votes removed (Developer approved): 71 live rounds were automated tests. 44 were answered faster than the page allows (under 0.9 s, including 40 "Both" answers from a reveal check). 27 were the Agent's headless-Edge runs on Nov 16 (only "Neither", matching the test counts). `--void-rounds` subtracted their 142 votes from 97 images and marked the rounds `void`; a rerun voided 0. Afterwards the image vote total equalled 2 × real answered rounds (850 = 850). From now on the Agent runs browser tests against the local server only, or voids its live test rounds afterwards.
- Reveal credit (Developer: "don't credit the AI works, just the % who got it"): 11 live AI images were CC BY / BY-SA, so the Developer chose to keep them with a small License link. Over 40 local rounds the answer response carried no title, artist or link for uncredited AI images, and full credit for CC BY AI images and human works. The labels read "25% of 4 players got it right" (AI) and keep the full credit for human works (headless Edge, desktop and phone).
- Developer's own AI images: `--add-own` added. A local run passed all 6 (kitchen, rainy street, sleeping dog, pears, salt marsh, portrait) through the normalization, duplicate and text checks. The local test files were moved to `scripts/raw/local-test-img/` and their local rows hidden by `--check-files --local`.
- Nightly growth raised to 20 human + 15 AI per run (Developer, 2026-11-17), from 10 + 5. Before the change: 286 active images, 789 votes, 105 images with 3+ votes. Nothing is ever reset; images leave only by reports, `--reject`, or an unservable file.
- Logo 1.5× (Developer chose "logo above the streak"): on desktop the logo is 75 px at the top of the clock post and the header is gone, so the artwork grew from 460 to 500 px. On phones it stays in the header at 66 px, and the artwork shrank from 270 to 259 px.
- Phone side names: the plaques read "Top is AI" / "Bottom is AI", the verdict says "The answer was 'Top is AI'", and the image descriptions say Top/Bottom artwork. Desktop is unchanged (Left/Right). Resizing a desktop window to phone width switches the names live. Checked in headless Edge.
- Header (Developer request): the PENCILSLOP text was removed and the logo enlarged (desktop 42 → 50 px, phone 38 → 44 px). The console's reserved height shrank by the same amount, so the artwork stayed 460 px on desktop and went 272 → 270 px on phone.
- Unservable images (Developer: "if an image can't be used or doesn't appear, remove it"): all 286 live active images were fetched and decoded as 640×640 JPEGs, with no blank ones, so the missing images were the 9 local test rows with no file. `/api/missing`: a working image was kept, a missing one hidden and never served in the next 300 rounds, a bad side gave 400 and an unknown round 404. 40 local rounds in headless Edge: the page hit 1 broken image, reported it and swapped in another pair, and no round opened with a broken image. `--check-files --local` hid the other 7 local rows; `--check-files --remote` checked 286 and hid 0.
- Hid 2 seed AI images titled "logo" (`882b21b99e8733a5`, `295ba86aafa0d13d`, a round badge on white) with `--reject`. They predate the title rules. Live AI count: 61.
- Repeats: one simulated 40-round run with `seen` served 80 of 80 unique images; without `seen`, 65 of 80. A junk `seen` list is ignored (200).

## Simplification reviews

None yet (first review at the end of Phase 3).
