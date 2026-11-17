// PENCILSLOP Worker: game API. Static files in public/ (page, scripts, and the
// artwork images in public/img/) are served by Cloudflare's asset handler; only
// /api/* requests reach this code.
//
//   POST /api/round   { streak, seen } -> { roundId, left, right, seconds }   (no answer included)
//   POST /api/answer  { roundId, choice, countLeft, countRight } -> truth, credits, prior crowd votes
//                     (countLeft/countRight false: this device already voted on that image)
//   POST /api/report  { roundId, side }   -> flag an image from an answered round
//   POST /api/missing { roundId, side }   -> an image failed to load; hidden if its file is gone

const ROUND_SECONDS = 10;
const GRACE_MS = 3000;                 // allowance for network latency on the timer
const AI_CHANCE_MIN = 0.2;             // each round picks its AI chance in this range
const AI_CHANCE_MAX = 0.6;
const REPORTS_TO_HIDE = 3;
const MAX_SEEN = 300;                  // most image IDs a run can ask to skip             // an image with this many reports stops being served
const CHOICES = ["left", "right", "both", "neither", "timeout"];

// Difficulty: each slot picks a tier (1 easy, 2 medium, 3 hard; set by ingest.py)
// with odds that depend on the player's streak. Between these rows the odds are
// interpolated linearly; past the last row they stay fixed. Approved 2026-11-16.
const TIER_ODDS = [
  // streak  [easy, medium, hard]
  { streak: 0,  odds: [0.50, 0.30, 0.20] },
  { streak: 5,  odds: [0.30, 0.45, 0.25] },
  { streak: 10, odds: [0.15, 0.40, 0.45] },
  { streak: 15, odds: [0.10, 0.25, 0.65] },
];

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    try {
      if (request.method === "POST" && pathname === "/api/round") return await newRound(request, env);
      if (request.method === "POST" && pathname === "/api/answer") return await answer(request, env);
      if (request.method === "POST" && pathname === "/api/report") return await report(request, env);
      if (request.method === "POST" && pathname === "/api/missing") return await missing(request, env);
      return json({ error: "Not found" }, 404);
    } catch (err) {
      console.error(err);
      return json({ error: "Server error" }, 500);
    }
  },
};

// Serve a new pair. Each slot is independently AI with this round's chance, and
// independently picks a tier by the streak. The streak comes from the browser; a
// faked streak only changes which images are shown, never the answer. `seen` lists
// the image IDs already shown this run, so a run doesn't repeat images.
async function newRound(request, env) {
  const body = await request.json().catch(() => ({}));
  const streak = Number.isInteger(body.streak) && body.streak > 0 ? body.streak : 0;
  const seen = Array.isArray(body.seen)
    ? body.seen.filter((id) => typeof id === "string" && /^[0-9a-f]{16}$/.test(id)).slice(-MAX_SEEN)
    : [];
  const odds = tierOdds(streak);

  const chance = AI_CHANCE_MIN + Math.random() * (AI_CHANCE_MAX - AI_CHANCE_MIN);
  const leftAI = Math.random() < chance;
  const rightAI = Math.random() < chance;

  const left = await randomImage(env, leftAI, pickTier(odds), seen);
  const right = await randomImage(env, rightAI, pickTier(odds), left ? [...seen, left] : seen);
  if (!left || !right) return json({ error: "Catalog too small" }, 500);

  const roundId = crypto.randomUUID();
  await env.DB.prepare(
    "INSERT INTO rounds (id, left_id, right_id, created_at) VALUES (?, ?, ?, ?)"
  ).bind(roundId, left, right, Date.now()).run();

  return json({ roundId, left: `/img/${left}.jpg`, right: `/img/${right}.jpg`, seconds: ROUND_SECONDS });
}

// Odds for a streak, interpolated between the TIER_ODDS rows.
function tierOdds(streak) {
  const last = TIER_ODDS[TIER_ODDS.length - 1];
  if (streak >= last.streak) return last.odds;
  const i = TIER_ODDS.findIndex((row) => row.streak > streak);
  const lo = TIER_ODDS[i - 1], hi = TIER_ODDS[i];
  const t = (streak - lo.streak) / (hi.streak - lo.streak);
  return lo.odds.map((p, k) => p + t * (hi.odds[k] - p));
}

function pickTier(odds) {
  let r = Math.random();
  for (let k = 0; k < odds.length; k++) {
    r -= odds[k];
    if (r < 0) return k + 1;
  }
  return odds.length;
}

// A random active image of the given kind and tier, not in `excludeIds`. If that
// tier has none of this kind, any tier is used, so a thin tier never breaks a
// round. The exclusions go as one JSON parameter (D1 caps bound parameters at 100).
// If a long run has seen every image of this kind, repeats are allowed again.
async function randomImage(env, isAI, tier, excludeIds) {
  const pick = (exclude) => env.DB.prepare(
    `SELECT id FROM images WHERE active = 1 AND is_ai = ?
       AND id NOT IN (SELECT value FROM json_each(?))
     ORDER BY (tier = ?) DESC, RANDOM() LIMIT 1`
  ).bind(isAI ? 1 : 0, JSON.stringify(exclude), tier).first();
  const row = (await pick(excludeIds)) ?? (await pick(excludeIds.slice(-1)));
  return row?.id ?? null;
}

// Check an answer, record votes, and reveal. Crowd numbers are the counts
// from before this vote ("what earlier players thought").
async function answer(request, env) {
  const body = await request.json().catch(() => ({}));
  const { roundId } = body;
  let { choice } = body;
  // One vote per device per image: the browser says which of its votes are repeats.
  // Older pages don't send these, so a missing flag means "count it".
  const countLeft = body.countLeft !== false;
  const countRight = body.countRight !== false;
  if (typeof roundId !== "string" || !CHOICES.includes(choice)) {
    return json({ error: "Bad request" }, 400);
  }

  const round = await env.DB.prepare("SELECT * FROM rounds WHERE id = ?").bind(roundId).first();
  if (!round) return json({ error: "Unknown round" }, 404);
  if (round.choice !== null) return json({ error: "Round already answered" }, 409);

  // The server, not the browser, decides whether time ran out.
  const now = Date.now();
  if (now - round.created_at > ROUND_SECONDS * 1000 + GRACE_MS) choice = "timeout";

  // Mark answered only if still unanswered, so two simultaneous submissions can't both count.
  const claim = await env.DB.prepare(
    "UPDATE rounds SET choice = ?, answered_at = ?, left_counted = ?, right_counted = ? WHERE id = ? AND choice IS NULL"
  ).bind(choice, now, countLeft ? 1 : 0, countRight ? 1 : 0, roundId).run();
  if (claim.meta.changes === 0) return json({ error: "Round already answered" }, 409);

  const { results } = await env.DB.prepare(
    "SELECT id, is_ai, title, artist, source_url, license, ai_evidence, votes_ai, votes_real FROM images WHERE id IN (?, ?)"
  ).bind(round.left_id, round.right_id).all();
  const byId = Object.fromEntries(results.map((r) => [r.id, r]));
  const left = byId[round.left_id];
  const right = byId[round.right_id];

  const correctChoice =
    left.is_ai && right.is_ai ? "both" : left.is_ai ? "left" : right.is_ai ? "right" : "neither";

  // A timeout casts no votes: the player didn't judge either image. A repeat vote
  // from the same device on the same image isn't counted either.
  if (choice !== "timeout") {
    const votes = [];
    if (countLeft) votes.push(voteStatement(env, left.id, choice === "left" || choice === "both"));
    if (countRight) votes.push(voteStatement(env, right.id, choice === "right" || choice === "both"));
    if (votes.length) await env.DB.batch(votes);
  }

  return json({
    correct: choice === correctChoice,
    choice,
    correctChoice,
    left: reveal(left),
    right: reveal(right),
  });
}

// A player flags an image as unfair (watermark, text, mislabeled...). Only images
// from a round that has been answered can be reported, once per image per round.
// At REPORTS_TO_HIDE reports the image stops being served.
async function report(request, env) {
  const { roundId, side } = await request.json().catch(() => ({}));
  if (typeof roundId !== "string" || (side !== "left" && side !== "right")) {
    return json({ error: "Bad request" }, 400);
  }
  const round = await env.DB.prepare("SELECT left_id, right_id, choice FROM rounds WHERE id = ?").bind(roundId).first();
  if (!round) return json({ error: "Unknown round" }, 404);
  if (round.choice === null) return json({ error: "Answer the round first" }, 409);

  const imageId = side === "left" ? round.left_id : round.right_id;
  const inserted = await env.DB.prepare(
    "INSERT OR IGNORE INTO reports (round_id, image_id, created_at) VALUES (?, ?, ?)"
  ).bind(roundId, imageId, Date.now()).run();
  if (inserted.meta.changes === 0) return json({ ok: true, duplicate: true });

  await env.DB.prepare(
    `UPDATE images SET reports = reports + 1,
       active = CASE WHEN reports + 1 >= ? THEN 0 ELSE active END
     WHERE id = ?`
  ).bind(REPORTS_TO_HIDE, imageId).run();
  return json({ ok: true });
}

// The page couldn't load an image. The Worker checks the deployed file itself, so
// a player can't hide a working image this way; only a missing file is hidden.
async function missing(request, env) {
  const { roundId, side } = await request.json().catch(() => ({}));
  if (typeof roundId !== "string" || (side !== "left" && side !== "right")) {
    return json({ error: "Bad request" }, 400);
  }
  const round = await env.DB.prepare("SELECT left_id, right_id FROM rounds WHERE id = ?").bind(roundId).first();
  if (!round) return json({ error: "Unknown round" }, 404);

  const imageId = side === "left" ? round.left_id : round.right_id;
  const file = await env.ASSETS.fetch(new URL(`/img/${imageId}.jpg`, request.url));
  if (file.ok) return json({ hidden: false });
  await env.DB.prepare("UPDATE images SET active = 0 WHERE id = ?").bind(imageId).run();
  return json({ hidden: true });
}

function voteStatement(env, id, judgedAI) {
  const column = judgedAI ? "votes_ai" : "votes_real";
  return env.DB.prepare(`UPDATE images SET ${column} = ${column} + 1 WHERE id = ?`).bind(id);
}

// What the reveal shows. Human works are credited in full. AI images are not
// credited (Developer, 2026-11-17), except that a license requiring attribution
// (CC BY, CC BY-SA) still gets its source link.
const ATTRIBUTION_REQUIRED = /\bCC BY\b/i;

function reveal(img) {
  const shown = {
    isAI: img.is_ai === 1,
    votesAI: img.votes_ai,
    votesReal: img.votes_real,
  };
  if (!shown.isAI || ATTRIBUTION_REQUIRED.test(img.license)) {
    Object.assign(shown, { title: img.title, artist: img.artist, sourceUrl: img.source_url, license: img.license });
  }
  return shown;
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}
